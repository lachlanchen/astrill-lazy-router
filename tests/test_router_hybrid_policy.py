from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "router" / "alctl"
HYBRID = ROOT / "router" / "alhybrid"


def _shell() -> str | None:
    found = shutil.which("sh")
    if found is not None:
        return found
    candidate = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
    return str(candidate) if candidate.exists() else None


SHELL = _shell()


def _functions() -> str:
    source = CONTROLLER.read_text(encoding="ascii")
    functions, marker, _dispatch = source.partition("\ncommand=${1:-}\n")
    assert marker
    return functions


def _run(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    if SHELL is None:
        pytest.skip("POSIX shell is unavailable")
    script = tmp_path / "scenario.sh"
    script.write_text(
        _functions() + "\n" + scenario,
        encoding="ascii",
        newline="\n",
    )
    path = os.environ.get("PATH", "")
    if os.name == "nt":
        path = "/usr/bin:/bin"
    environment = {
        **os.environ,
        "PATH": path,
        "ASTRILL_LAZY_BASE": (tmp_path / "runtime").as_posix(),
        "ASTRILL_LAZY_ARP_FILE": (tmp_path / "arp").as_posix(),
        "ASTRILL_LAZY_HYBRID_HELPER": HYBRID.as_posix(),
        "ASTRILL_LAZY_IPTABLES_RESTORE": (
            tmp_path / "missing-iptables-restore"
        ).as_posix(),
    }
    return subprocess.run(
        [SHELL, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_single_pass_overlay_validator_accepts_valid_document_and_rejects_malformed(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/valid-overlay.tsv" <<'EOF'
# astrill-lazy-rules-v1
domain-row	1	10	domain	example.com	direct	tcp	443	Domain	domain-origin
cidr-row	1	20	cidr	203.0.113.0/24	vpn	any	-	Cidr	cidr-origin
EOF
hybrid_validate_rules "$BASE/valid-overlay.tsv" || exit 9

: > "$BASE/empty-overlay.tsv"
hybrid_validate_rules "$BASE/empty-overlay.tsv" && exit 10

cat > "$BASE/bad-source.tsv" <<'EOF'
# astrill-lazy-rules-v1
bad-source	1	10	cidr	999.0.113.0/24	direct	any	-	Bad	bad-source
EOF
hybrid_validate_rules "$BASE/bad-source.tsv" && exit 11

cat > "$BASE/device-overlay.tsv" <<'EOF'
# astrill-lazy-rules-v1
device-row	1	10	device	192.168.1.50/32	direct	any	-	Device	device-origin
EOF
hybrid_validate_rules "$BASE/device-overlay.tsv" && exit 12

cat > "$BASE/bad-fields.tsv" <<'EOF'
# astrill-lazy-rules-v1
bad-fields	1	10	domain	example.com	direct	tcp	443	Label
EOF
hybrid_validate_rules "$BASE/bad-fields.tsv" && exit 13
printf 'overlay-validator-safe\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "overlay-validator-safe\n"


def test_single_pass_effective_validator_accepts_valid_scopes_and_rejects_bad_source_or_mac(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/valid-effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin	-	-
overlay-row	1	20	domain	example.com	vpn	tcp	443	Overlay	overlay-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff
EOF
hybrid_validate_effective "$BASE/valid-effective.tsv" || exit 9

: > "$BASE/empty-effective.tsv"
hybrid_validate_effective "$BASE/empty-effective.tsv" && exit 10

cat > "$BASE/bad-effective-source.tsv" <<'EOF'
# astrill-lazy-effective-v1
overlay-row	1	20	domain	example.com	vpn	tcp	443	Overlay	overlay-origin	192.168.1.999/32	aa:bb:cc:dd:ee:ff
EOF
hybrid_validate_effective "$BASE/bad-effective-source.tsv" && exit 11

cat > "$BASE/bad-effective-mac.tsv" <<'EOF'
# astrill-lazy-effective-v1
overlay-row	1	20	domain	example.com	vpn	tcp	443	Overlay	overlay-origin	192.168.1.50/32	gg:bb:cc:dd:ee:ff
EOF
hybrid_validate_effective "$BASE/bad-effective-mac.tsv" && exit 12

cat > "$BASE/mac-without-source.tsv" <<'EOF'
# astrill-lazy-effective-v1
overlay-row	1	20	domain	example.com	vpn	tcp	443	Overlay	overlay-origin	-	aa:bb:cc:dd:ee:ff
EOF
hybrid_validate_effective "$BASE/mac-without-source.tsv" && exit 13
printf 'effective-validator-safe\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "effective-validator-safe\n"


def test_ram_helper_composes_source_and_mac_guarded_matches(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin
EOF
cat > "$BASE/owner.tsv" <<'EOF'
# astrill-lazy-rules-v1
owner-row	1	20	cidr	2.2.2.2/32	vpn	tcp	443	Owner	owner-origin
EOF
hybrid_compose_effective \
    "$CURRENT" "$BASE/composed.tsv" owner "$BASE/owner.tsv" \
    192.168.1.50/32 aa:bb:cc:dd:ee:ff || exit 9
TRACE=$BASE/iptables.trace
: > "$TRACE"
ensure_chain_shell() { return 0; }
iptables() {
    printf '%s\n' "$*" >> "$TRACE"
    return 0
}
build_chain AL_TEST "$BASE/composed.tsv" || exit 10
cat "$BASE/composed.tsv"
cat "$TRACE"
""",
    )

    assert result.returncode == 0, result.stderr
    owner_mark = next(
        line
        for line in result.stdout.splitlines()
        if "2.2.2.2/32" in line and "--set-xmark" in line
    )
    assert (
        "-s 192.168.1.50/32 -m mac --mac-source aa:bb:cc:dd:ee:ff "
        "-d 2.2.2.2/32 -p tcp --dport 443"
    ) in owner_mark
    core_mark = next(
        line
        for line in result.stdout.splitlines()
        if "1.1.1.1/32" in line and "--set-xmark" in line
    )
    assert "--mac-source" not in core_mark


def test_ram_helper_tests_and_commits_one_batched_restore_document(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin	-	-
owner-row	1	20	cidr	2.2.2.2/32	vpn	tcp	443	Owner	owner-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff
EOF
RESTORE_TRACE=$BASE/restore.trace
RESTORE_DOCUMENTS=$BASE/restore.documents
export RESTORE_TRACE RESTORE_DOCUMENTS
IPTABLES_RESTORE=$BASE/iptables-restore
cat > "$IPTABLES_RESTORE" <<'EOF'
#!/bin/sh
printf 'call:%s\n' "$*" >> "$RESTORE_TRACE"
for argument in "$@"; do restore_file=$argument; done
cat "$restore_file" >> "$RESTORE_DOCUMENTS"
printf '%s\n' --- >> "$RESTORE_DOCUMENTS"
EOF
chmod 700 "$IPTABLES_RESTORE"
hybrid_policy_free_kib() { printf '50000\n'; }
chain_exists() { return 0; }
hybrid_restore_topology_ok() { return 0; }
iptables() {
    case "$*" in
        *"-S AL_TEST")
            count=0
            while [ "$count" -lt 10 ]; do
                printf '%s\n' "-A AL_TEST -j RETURN"
                count=$((count + 1))
            done
            ;;
        *) printf '%s\n' "$*" >> "$BASE/fallback" ;;
    esac
}
build_chain AL_TEST "$BASE/effective.tsv" || exit 9
[ ! -e "$BASE/fallback" ] || exit 10
[ "$(grep -c '^call:' "$RESTORE_TRACE")" -eq 2 ] || exit 11
sed -n '1p' "$RESTORE_TRACE" | grep -q ' -t ' || exit 12
sed -n '2p' "$RESTORE_TRACE" | grep -q ' -t ' && exit 13
grep -q '^:AL_TEST - \[0:0\]$' "$RESTORE_DOCUMENTS" || exit 14
grep -q '^:PREROUTING ' "$RESTORE_DOCUMENTS" && exit 15
grep -q '^-F ' "$RESTORE_DOCUMENTS" && exit 16
grep -q -- '-s 192.168.1.50/32 -m mac --mac-source aa:bb:cc:dd:ee:ff -d 2.2.2.2/32 -p tcp --dport 443 -j MARK' \
    "$RESTORE_DOCUMENTS" || exit 17
grep -q -- '-d 2.2.2.2/32 -p tcp --dport 443 -j RETURN' \
    "$RESTORE_DOCUMENTS" || exit 18
[ "$(grep -c '^COMMIT$' "$RESTORE_DOCUMENTS")" -eq 2 ] || exit 19
[ "$(cat "$GENERATED_MATCHES_FILE")" -eq 2 ] || exit 20
printf 'batched-restore\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "batched-restore\n"


def test_runtime_plan_deduplicates_equivalent_source_scoped_matches(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
first	1	10	cidr	2.2.2.2/32	direct	any	-	First	first-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff
second	1	20	cidr	2.2.2.2/32	direct	any	-	Second	second-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff
EOF
TRACE=$BASE/iptables.trace
: > "$TRACE"
ensure_chain_shell() { return 0; }
iptables() {
    printf '%s\n' "$*" >> "$TRACE"
    return 0
}
build_chain AL_TEST "$BASE/effective.tsv" || exit 9
[ "$(cat "$GENERATED_MATCHES_FILE")" -eq 1 ] || exit 10
[ "$(grep -c 'MARK --set-xmark' "$TRACE")" -eq 1 ] || exit 11
[ "$(grep -c 'origin' "$BASE/effective.tsv")" -eq 2 ] || exit 12
printf 'runtime-plan-deduplicated\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "runtime-plan-deduplicated\n"


def test_restore_topology_accepts_cold_start_and_misordered_bare_hook(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
STATE=$BASE/mangle.state
iptables() {
    case "$*" in
        *"-S PREROUTING")
            printf '%s\n' '-P PREROUTING ACCEPT'
            awk '$1 == "-A" && $2 == "PREROUTING" { print }' "$STATE"
            ;;
        *"-S") cat "$STATE" ;;
        *) return 1 ;;
    esac
}

cat > "$STATE" <<'EOF'
-N AL_LAZY_A
-N AL_LAZY_B
EOF
hybrid_restore_topology_ok "$CHAIN_B" || exit 9

cat > "$STATE" <<'EOF'
-N AL_LAZY_A
-N AL_LAZY_B
-A PREROUTING -j SOME_DD_WRT_CHAIN
-A PREROUTING -j AL_LAZY_A
EOF
hybrid_restore_topology_ok "$CHAIN_B" || exit 10
printf 'safe-topologies-accepted\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "safe-topologies-accepted\n"


def test_restore_topology_rejects_conditional_duplicate_and_inactive_refs(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
STATE=$BASE/mangle.state
iptables() {
    case "$*" in
        *"-S PREROUTING")
            printf '%s\n' '-P PREROUTING ACCEPT'
            awk '$1 == "-A" && $2 == "PREROUTING" { print }' "$STATE"
            ;;
        *"-S") cat "$STATE" ;;
        *) return 1 ;;
    esac
}

cat > "$STATE" <<'EOF'
-N AL_LAZY_A
-N AL_LAZY_B
-A PREROUTING -s 192.168.1.0/24 -j AL_LAZY_A
EOF
hybrid_restore_topology_ok "$CHAIN_B" && exit 9

cat > "$STATE" <<'EOF'
-N AL_LAZY_A
-N AL_LAZY_B
-A PREROUTING -j AL_LAZY_A
-A PREROUTING -j AL_LAZY_A
EOF
hybrid_restore_topology_ok "$CHAIN_B" && exit 10

cat > "$STATE" <<'EOF'
-N AL_LAZY_A
-N AL_LAZY_B
-N OTHER_CHAIN
-A PREROUTING -j AL_LAZY_A
-A OTHER_CHAIN -j AL_LAZY_B
EOF
hybrid_restore_topology_ok "$CHAIN_B" && exit 11
printf 'unsafe-topologies-rejected\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "unsafe-topologies-rejected\n"


def test_restore_topology_fails_closed_when_mangle_rules_are_unreadable(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
iptables() { return 1; }
hybrid_chain_reference_count "$CHAIN_A" >/dev/null 2>&1 && exit 9
hybrid_restore_topology_ok "$CHAIN_B" >/dev/null 2>&1 && exit 10
printf 'unreadable-rules-rejected\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "unreadable-rules-rejected\n"


def test_failed_restore_validation_never_commits_or_publishes_runtime_state(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	cidr	1.1.1.1/32	direct	any	-	Row	origin	-	-
EOF
printf 'old-resolved\n' > "$RESOLVED"
printf 'old-unresolved\n' > "$UNRESOLVED"
printf '77\n' > "$GENERATED_MATCHES_FILE"
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
RESTORE_TRACE=$BASE/restore.trace
export RESTORE_TRACE
IPTABLES_RESTORE=$BASE/iptables-restore
cat > "$IPTABLES_RESTORE" <<'EOF'
#!/bin/sh
mode=commit
for argument in "$@"; do
    [ "$argument" != -t ] || mode=test
done
printf '%s\n' "$mode" >> "$RESTORE_TRACE"
[ "$mode" != test ]
EOF
chmod 700 "$IPTABLES_RESTORE"
hybrid_policy_free_kib() { printf '50000\n'; }
hybrid_restore_topology_ok() { return 0; }
iptables() { return 0; }
build_chain "$CHAIN_B" "$BASE/effective.tsv" >/dev/null 2>&1 && exit 9
[ "$(cat "$RESTORE_TRACE")" = test ] || exit 10
[ "$(cat "$RESOLVED")" = old-resolved ] || exit 11
[ "$(cat "$UNRESOLVED")" = old-unresolved ] || exit 12
[ "$(cat "$GENERATED_MATCHES_FILE")" = 77 ] || exit 13
[ "$(cat "$ACTIVE_FILE")" = "$CHAIN_A" ] || exit 14
[ -z "$(find "$BASE" -maxdepth 1 \
    \( -name 'iptables-restore.*' -o -name 'hybrid-plan.*' -o \
       -name 'resolved.new.*' -o -name 'unresolved.new.*' -o \
       -name 'generated-matches.new.*' \) -print -quit)" ] || exit 15
printf 'restore-test-failed-closed\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "restore-test-failed-closed\n"


def test_refresh_activation_failure_restores_runtime_metadata(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 7
cat > "$EFFECTIVE" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	cidr	1.1.1.1/32	direct	any	-	Row	origin	-	-
EOF
printf 'old-resolved\n' > "$RESOLVED"
printf 'old-unresolved\n' > "$UNRESOLVED"
printf '77\n' > "$GENERATED_MATCHES_FILE"
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"

build_chain() {
    [ "$1" = "$CHAIN_B" ] || return 1
    [ "$2" = "$EFFECTIVE" ] || return 1
    [ "$3" = 1 ] || return 1
    printf 'new-resolved\n' > "$RESOLVED"
    printf 'new-unresolved\n' > "$UNRESOLVED"
    printf '99\n' > "$GENERATED_MATCHES_FILE"
}
ensure_routes() { return 0; }
discard_policy_chain() { printf '%s\n' "$1" > "$BASE/discarded"; }
activation_attempts=0
activate_chain() {
    activation_attempts=$((activation_attempts + 1))
    if [ "$1" = "$CHAIN_B" ]; then
        return 1
    fi
    [ "$1" = "$CHAIN_A" ] || return 1
    printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
}

apply_runtime "$EFFECTIVE" 1 >/dev/null 2>&1 && exit 8
[ "$(cat "$RESOLVED")" = old-resolved ] || exit 9
[ "$(cat "$UNRESOLVED")" = old-unresolved ] || exit 10
[ "$(cat "$GENERATED_MATCHES_FILE")" = 77 ] || exit 11
[ "$(cat "$ACTIVE_FILE")" = "$CHAIN_A" ] || exit 12
[ "$(cat "$BASE/discarded")" = "$CHAIN_B" ] || exit 13
[ ! -e "$RUNTIME_METADATA_SNAPSHOT" ] || exit 14
[ "$runtime_metadata_snapshot_active" = false ] || exit 15
printf 'refresh-metadata-restored\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "refresh-metadata-restored\n"


def test_dns_refresh_falls_back_to_cache_and_cancellation_cleans_jobs(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	domain	cached.example	direct	any	-	Row	origin	-	-
EOF
printf 'cached.example\t203.0.113.7\t1\n' > "$RESOLVED"
hybrid_started=$(date +%s)
hybrid_policy_free_kib() { printf '50000\n'; }
hybrid_resolve_domain_bounded() {
    printf 'attempt\n' >> "$BASE/dns-attempts"
    return 0
}
hybrid_prefetch_domains \
    "$BASE/effective.tsv" "$BASE/refreshed.tsv" 1 || exit 9
grep -q '^cached.example	203.0.113.7	' "$BASE/refreshed.tsv" || exit 10
[ "$(wc -l < "$BASE/dns-attempts")" -eq 1 ] || exit 11
[ -z "$(find "$BASE" -maxdepth 1 \
    \( -name 'hybrid-domains.*' -o -name 'hybrid-dns.*' \) \
    -print -quit)" ] || exit 12

sleep 30 &
cancelled_pid=$!
hybrid_dns_pids=$cancelled_pid
hybrid_dns_list=$BASE/hybrid-domains.$$.txt
hybrid_dns_dir=$BASE/hybrid-dns.$$
: > "$hybrid_dns_list"
mkdir "$hybrid_dns_dir"
sleep 30 &
cancelled_child_pid=$!
printf '%s\n' "$cancelled_child_pid" > "$hybrid_dns_dir/0.pid"
hybrid_cancel_dns_jobs || exit 13
kill -0 "$cancelled_pid" 2>/dev/null && exit 14
kill -0 "$cancelled_child_pid" 2>/dev/null && exit 15
[ ! -e "$hybrid_dns_list" ] || exit 16
[ ! -e "$hybrid_dns_dir" ] || exit 17
printf 'dns-fallback-and-cleanup\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "dns-fallback-and-cleanup\n"


def test_watchdog_cleanup_cancels_dns_and_restores_runtime_snapshot(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
printf 'old-resolved\n' > "$RESOLVED"
printf 'old-unresolved\n' > "$UNRESOLVED"
printf '77\n' > "$GENERATED_MATCHES_FILE"
printf '123\n' > "$BASE/last-apply"
capture_runtime_metadata || exit 9
printf 'new-resolved\n' > "$RESOLVED"
printf 'new-unresolved\n' > "$UNRESOLVED"
printf '99\n' > "$GENERATED_MATCHES_FILE"
printf '456\n' > "$BASE/last-apply"

sleep 30 &
cancelled_pid=$!
hybrid_dns_pids=$cancelled_pid
hybrid_dns_list=$BASE/hybrid-domains.$$.txt
hybrid_dns_dir=$BASE/hybrid-dns.$$
: > "$hybrid_dns_list"
mkdir "$hybrid_dns_dir"
sleep 30 &
cancelled_child_pid=$!
printf '%s\n' "$cancelled_child_pid" > "$hybrid_dns_dir/0.pid"

mkdir "$LOCK_DIR"
printf '%s\n' "$$" > "$LOCK_DIR/pid"
printf '%s\n' "$$" > "$WATCHDOG_PID"
watchdog_sleep_pid=0
cleanup_watchdog || exit 10

kill -0 "$cancelled_pid" 2>/dev/null && exit 11
kill -0 "$cancelled_child_pid" 2>/dev/null && exit 12
[ "$(cat "$RESOLVED")" = old-resolved ] || exit 13
[ "$(cat "$UNRESOLVED")" = old-unresolved ] || exit 14
[ "$(cat "$GENERATED_MATCHES_FILE")" = 77 ] || exit 15
[ "$(cat "$BASE/last-apply")" = 123 ] || exit 16
[ "$runtime_metadata_snapshot_active" = false ] || exit 17
[ -z "$(find "$BASE" -maxdepth 1 -name 'runtime-metadata.*' \
    -print -quit)" ] || exit 18
[ ! -e "$LOCK_DIR" ] || exit 19
[ ! -e "$WATCHDOG_PID" ] || exit 20
printf 'watchdog-cleanup-safe\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "watchdog-cleanup-safe\n"


def test_overlay_generation_and_source_ownership_never_write_nvram(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin
EOF
cp "$CURRENT" "$EFFECTIVE"
cat > "$BASE/shared.tsv" <<'EOF'
# astrill-lazy-rules-v1
shared-row	1	20	cidr	2.2.2.2/32	vpn	any	-	Shared	shared-origin
EOF
cat > "$BASE/collision.tsv" <<'EOF'
# astrill-lazy-rules-v1
collision-row	1	20	cidr	3.3.3.3/32	vpn	any	-	Bad	core-origin
EOF
cat > "$ARP_FILE" <<'EOF'
IP address HW type Flags HW address Mask Device
192.168.1.50 0x1 0x2 aa:bb:cc:dd:ee:ff * br0
EOF
NVRAM_WRITES=$BASE/nvram-writes
: > "$NVRAM_WRITES"
nvram() {
    if [ "$1" = get ]; then
        case "$2" in
            lan_ipaddr) printf '192.168.1.1' ;;
            lan_netmask) printf '255.255.255.0' ;;
            lan_ifname) printf 'br0' ;;
        esac
        return 0
    fi
    printf '%s\n' "$*" >> "$NVRAM_WRITES"
}
SSH_CONNECTION='192.168.1.50 55000 192.168.1.1 22'
printf 'AL_LAZY_A\n' > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() {
    cp "$1" "$BASE/applied.tsv"
    [ "$(active_chain)" = AL_LAZY_A ] &&
        printf 'AL_LAZY_B\n' > "$BASE/test-active" ||
        printf 'AL_LAZY_A\n' > "$BASE/test-active"
}
activate_chain() { printf '%s\n' "$1" > "$BASE/test-active"; }
hybrid_put_overlay first 0 auto "$BASE/shared.tsv" \
    192.168.1.50/32 aa:bb:cc:dd:ee:ff || exit 9
hybrid_load_meta first || exit 10
printf 'generation=%s source=%s mac=%s\n' \
    "$hybrid_generation" "$hybrid_source" "$hybrid_mac"
hybrid_put_overlay first 0 auto "$BASE/shared.tsv" 2>"$BASE/stale" &&
    exit 11
hybrid_put_overlay reassigned 0 auto "$BASE/shared.tsv" \
    192.168.1.50/32 00:11:22:33:44:55 2>"$BASE/reassigned" &&
    exit 15
grep -q 'overlay MAC binding changed' "$BASE/reassigned" || exit 16
hybrid_put_overlay second 0 192.168.1.50 "$BASE/shared.tsv" \
    2>/dev/null && exit 12
hybrid_put_overlay second 0 192.168.1.60 "$BASE/shared.tsv" || exit 13
[ "$(grep -c 'shared-origin' "$BASE/applied.tsv")" -eq 2 ] || exit 14
grep -q 'shared-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff' \
    "$BASE/applied.tsv" || exit 15
grep -q 'shared-origin	192.168.1.60/32	-' \
    "$BASE/applied.tsv" || exit 16
hybrid_put_overlay collision 0 192.168.1.70 "$BASE/collision.tsv" \
    2>/dev/null && exit 13
[ ! -s "$NVRAM_WRITES" ] || exit 17
cat "$BASE/stale"
printf 'overlays=%s no-nvram-writes\n' "$(hybrid_overlay_count)"
""",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "generation=1 source=192.168.1.50/32 mac=aa:bb:cc:dd:ee:ff"
    ) in result.stdout
    assert "overlay generation conflict: expected 0, current 1" in result.stdout
    assert result.stdout.endswith("overlays=2 no-nvram-writes\n")


def test_ddwrt_memory_and_ipv4_admission_avoid_32_bit_shell_limits(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
HYBRID_MEMINFO_FILE=$BASE/meminfo
cat > "$HYBRID_MEMINFO_FILE" <<'EOF'
MemFree:          33780 kB
MemAvailable:      6204 kB
Buffers:           1744 kB
Cached:            6288 kB
EOF
[ "$(hybrid_policy_free_kib)" = 41812 ] || exit 9
nvram() {
    case "$2" in
        lan_ipaddr) printf '192.168.1.1' ;;
        lan_netmask) printf '255.255.255.0' ;;
    esac
}
bounds=$(hybrid_network_bounds 192.168.1.0/24) || exit 10
[ "$bounds" = "3232235776	3232236031" ] || exit 11
hybrid_networks_overlap 192.168.1.10/32 192.168.1.0/24 || exit 12
hybrid_networks_overlap 192.168.2.0/24 192.168.1.0/24 && exit 13
hybrid_lan_contains 192.168.1.50/32 || exit 14
hybrid_lan_contains 192.168.2.50/32 && exit 15
printf 'free=%s bounds=%s\n' "$(hybrid_policy_free_kib)" "$bounds"
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "free=41812 bounds=3232235776\t3232236031\n"


def test_hybrid_build_rechecks_memory_after_inactive_chain_generation(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	cidr	1.1.1.1/32	direct	any	-	Row	origin	-	-
EOF
printf '0\n' > "$BASE/free-calls"
hybrid_policy_free_kib() {
    calls=$(cat "$BASE/free-calls")
    calls=$((calls + 1))
    printf '%s\n' "$calls" > "$BASE/free-calls"
    [ "$calls" -eq 1 ] && printf '9000\n' || printf '7000\n'
}
ensure_chain_shell() { return 0; }
iptables() { return 0; }
build_chain AL_TEST "$BASE/effective.tsv" && exit 9
[ "$(cat "$BASE/free-calls")" -eq 2 ] || exit 10
printf 'post-build-rejected\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "post-build-rejected\n"


def test_core_apply_uses_upstream_compressed_store_and_rolls_back(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old-row	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old-origin
EOF
cat > "$PREVIOUS" <<'EOF'
# astrill-lazy-rules-v1
older-row	1	10	cidr	9.9.9.9/32	direct	any	-	Older	older-origin
EOF
printf '1\n' > "$CORE_GENERATION_FILE"
cat > "$BASE/new.tsv" <<'EOF'
# astrill-lazy-rules-v1
new-row	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new-origin
EOF
NVRAM=$BASE/nvram
mkdir -p "$NVRAM"
nvram() {
    operation=$1
    key=${2:-}
    key=${key%%=*}
    case $operation in
        get) [ ! -f "$NVRAM/$2" ] || cat "$NVRAM/$2" ;;
        set) printf '%s' "${2#*=}" > "$NVRAM/$key" ;;
        unset) rm -f "$NVRAM/$2" ;;
        commit)
            count=$(cat "$NVRAM/commits" 2>/dev/null || printf 0)
            printf '%s\n' $((count + 1)) > "$NVRAM/commits"
            ;;
        show) printf 'size: 1 bytes (100000 left)\n' >&2 ;;
    esac
}
verify_persisted_rule_document() { return 0; }
load_hybrid_helper || exit 17
encode_rule_document() { printf 'GZ'; }
persist_rules() {
    persist_rule_document \
        "$CURRENT" "$CURRENT_RULES_KEY" "$CURRENT_RULES_GZ_KEY" &&
        persist_rule_document \
            "$PREVIOUS" "$PREVIOUS_RULES_KEY" "$PREVIOUS_RULES_GZ_KEY" &&
        nvram commit
}
hybrid_persist_rules() { persist_rules; }
printf 'AL_LAZY_A\n' > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() {
    [ "$(active_chain)" = AL_LAZY_A ] &&
        printf 'AL_LAZY_B\n' > "$BASE/test-active" ||
        printf 'AL_LAZY_A\n' > "$BASE/test-active"
}
activate_chain() { printf '%s\n' "$1" > "$BASE/test-active"; }
apply_file "$BASE/new.tsv" || exit 8
grep -q new-origin "$CURRENT" || exit 9
grep -q old-origin "$PREVIOUS" || exit 10
[ "$(nvram get "$CURRENT_RULES_GZ_KEY")" = GZ ] || exit 11
[ "$(nvram get "$PREVIOUS_RULES_GZ_KEY")" = GZ ] || exit 12
[ ! -f "$NVRAM/$CURRENT_RULES_KEY" ] || exit 13
cp "$PREVIOUS" "$BASE/rollback.tsv"
apply_file "$BASE/rollback.tsv" || exit 14
grep -q old-origin "$CURRENT" || exit 15
grep -q new-origin "$PREVIOUS" || exit 16
printf 'commits=%s generation=%s\n' \
    "$(cat "$NVRAM/commits")" "$(cat "$CORE_GENERATION_FILE")"
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "commits=2 generation=3\n"


def test_core_persistence_failure_restores_files_and_previous_chain(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old-row	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old-origin
EOF
cat > "$PREVIOUS" <<'EOF'
# astrill-lazy-rules-v1
older-row	1	10	cidr	9.9.9.9/32	direct	any	-	Older	older-origin
EOF
printf '1\n' > "$CORE_GENERATION_FILE"
cat > "$BASE/candidate.tsv" <<'EOF'
# astrill-lazy-rules-v1
new-row	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new-origin
EOF
load_hybrid_helper || exit 12
check_persistence_capacity() { PERSIST_PREVIOUS=1; }
hybrid_capture_persistent_state() {
    for key in \
        "$CURRENT_RULES_KEY" "$CURRENT_RULES_GZ_KEY" \
        "$PREVIOUS_RULES_KEY" "$PREVIOUS_RULES_GZ_KEY"; do
        : > "$1.$key"
    done
}
hybrid_restore_persistent_state() { return 0; }
discard_policy_chain() { return 0; }
printf 'AL_LAZY_A\n' > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() { printf 'AL_LAZY_B\n' > "$BASE/test-active"; }
hybrid_persist_rules() { return 1; }
activate_chain() {
    printf '%s\n' "$1" > "$BASE/test-active"
    printf '%s\n' "$1" > "$BASE/restored"
}
apply_file "$BASE/candidate.tsv" 2>/dev/null && exit 8
[ "$(cat "$BASE/restored")" = AL_LAZY_A ] || exit 9
grep -q old-origin "$CURRENT" || exit 10
grep -q older-origin "$PREVIOUS" || exit 11
printf 'restored\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "restored\n"


def test_overlay_commit_failure_restores_metadata_effective_and_chain(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin
EOF
cp "$CURRENT" "$EFFECTIVE"
cat > "$BASE/old.tsv" <<'EOF'
# astrill-lazy-rules-v1
old-row	1	20	cidr	2.2.2.2/32	vpn	any	-	Old	owner-origin
EOF
cat > "$BASE/new.tsv" <<'EOF'
# astrill-lazy-rules-v1
new-row	1	20	cidr	3.3.3.3/32	vpn	any	-	New	owner-origin
EOF
nvram() {
    case "$2" in
        lan_ipaddr) printf '192.168.1.1' ;;
        lan_netmask) printf '255.255.255.0' ;;
        lan_ifname) printf 'br0' ;;
    esac
}
printf 'AL_LAZY_A\n' > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() {
    [ "$(active_chain)" = AL_LAZY_A ] &&
        printf 'AL_LAZY_B\n' > "$BASE/test-active" ||
        printf 'AL_LAZY_A\n' > "$BASE/test-active"
}
activate_chain() {
    printf '%s\n' "$1" > "$BASE/test-active"
    printf '%s\n' "$1" > "$BASE/restored-chain"
}
hybrid_put_overlay owner 0 192.168.1.50 "$BASE/old.tsv" || exit 9
old_effective=$(document_hash "$EFFECTIVE")
FAIL_EFFECTIVE=true
mv() {
    if [ "$FAIL_EFFECTIVE" = true ] &&
       [ "${2:-}" = "$EFFECTIVE" ]; then
        return 1
    fi
    command mv "$@"
}
hybrid_put_overlay owner 1 192.168.1.50 "$BASE/new.tsv" 2>/dev/null &&
    exit 10
hybrid_load_meta owner || exit 11
[ "$hybrid_generation" = 1 ] || exit 12
[ "$(document_hash "$EFFECTIVE")" = "$old_effective" ] || exit 13
[ "$(cat "$BASE/restored-chain")" = AL_LAZY_B ] || exit 14
grep -q 2.2.2.2 "$hybrid_document" || exit 15
printf 'overlay-restored\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "overlay-restored\n"


def test_hybrid_helper_is_not_part_of_nvram_boot_package() -> None:
    installer = (ROOT / "desktop" / "astrill_lazy" / "installer.py").read_text(
        encoding="utf-8"
    )
    assert (
        '"alhybrid"' not in installer.partition("PACKAGE_FILES =")[2].partition(")")[0]
    )


def test_core_generation_cas_rejects_stale_writer_before_mutation(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old-row	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old-origin
EOF
cat > "$BASE/candidate.tsv" <<'EOF'
# astrill-lazy-rules-v1
new-row	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new-origin
EOF
printf '7\n' > "$CORE_GENERATION_FILE"
load_hybrid_helper || exit 8
hybrid_apply_core_file "$BASE/candidate.tsv" 6 2>"$BASE/error" && exit 9
grep -q 'core generation conflict: expected 6, current 7' "$BASE/error" ||
    exit 10
grep -q old-origin "$CURRENT" || exit 11
grep -q new-origin "$CURRENT" && exit 12
printf 'cas-rejected\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "cas-rejected\n"


def test_corrupt_current_core_recovers_verified_previous_and_marks_degraded(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
cat > "$BASE/previous-store.tsv" <<'EOF'
# astrill-lazy-rules-v1
recovery-row	1	10	cidr	9.9.9.9/32	direct	any	-	Recovery	recovery-origin
EOF
nvram() {
    [ "$1" = get ] || return 0
    case "$2" in
        "$CURRENT_RULES_GZ_KEY") printf 'not-valid-base64' ;;
        "$PREVIOUS_RULES_KEY") cat "$BASE/previous-store.tsv" ;;
    esac
}
initialize_rules || exit 8
validate_rules "$CURRENT" || exit 9
grep -q recovery-origin "$CURRENT" || exit 10
grep -q 'running verified previous core' "$CORE_RECOVERY_FILE" || exit 11
grep -q recovery-origin "$PREVIOUS" || exit 12
printf 'recovered-degraded\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "recovered-degraded\n"


def test_core_recovery_marker_makes_router_status_degraded(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        r"""
initialize_rules() {
    printf '# astrill-lazy-rules-v1\n' > "$CURRENT"
    printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
    printf '1\n' > "$CORE_GENERATION_FILE"
}
initialize_app_flows() { : > "$APP_FLOWS"; }
load_hybrid_helper() { return 1; }
active_chain() { printf '%s\n' "$CHAIN_A"; }
printf '%s\n' 11111111111111111111111111111111 > "$PACKAGE_MD5_FILE"
nvram() {
    case "${2:-}" in
        astrill_status) printf 0 ;;
        astrill_serverid|astrill_protocol) printf 1 ;;
        astrill_lazy_pkg_md5) printf 22222222222222222222222222222222 ;;
        wan_iface) printf vlan2 ;;
        *) printf '' ;;
    esac
}
tunnel_is_up() { return 1; }
watchdog_running() { return 0; }
iptables() { return 0; }
direct_table_is_ready() { return 0; }
vpn_table_is_ready() { return 0; }
vpn_fail_closed_is_ready() { return 0; }
native_min_pref() { return 0; }
current_owned_pref() { return 0; }
owned_rule_count() { printf '0\n'; }
printf '%s\n' 'persistent current core was invalid' > "$CORE_RECOVERY_FILE"
status_json
""",
    )

    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["policy_health"] == "degraded"
    assert status["health"] == "degraded"
    assert status["core_recovery"] == "persistent current core was invalid"
    assert status["package_md5"] == "1" * 32
    assert status["stored_package_md5"] == "2" * 32
    assert len(status["helper_md5"]) == 32


def test_current_runtime_recomposes_valid_but_stale_effective_document(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
printf '1\n' > "$CORE_GENERATION_FILE"
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core-row	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core-origin
EOF
cat > "$OVERLAY_DIR/owner.1.tsv" <<'EOF'
# astrill-lazy-rules-v1
overlay-row	1	20	cidr	2.2.2.2/32	vpn	any	-	Overlay	overlay-origin
EOF
hybrid_write_meta \
    "$OVERLAY_DIR/owner.meta" 1 192.168.1.50/32 aa:bb:cc:dd:ee:ff \
    "$OVERLAY_DIR/owner.1.tsv" || exit 9
cat > "$EFFECTIVE" <<'EOF'
# astrill-lazy-effective-v1
stale-row	1	20	cidr	8.8.8.8/32	vpn	any	-	Stale	stale-origin	192.168.1.50/32	aa:bb:cc:dd:ee:ff
EOF
runtime=$(current_runtime_document) || exit 10
[ "$runtime" = "$EFFECTIVE" ] || exit 11
grep -q 2.2.2.2 "$EFFECTIVE" || exit 12
grep -q 8.8.8.8 "$EFFECTIVE" && exit 13
printf 'recomposed\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "recomposed\n"


def test_match_quota_is_rejected_before_any_iptables_mutation(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
MAX_GENERATED_MATCHES=1
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	cidr	1.1.1.1/32	direct	tcp	80,81	Row	origin	-	-
EOF
hybrid_policy_free_kib() { printf '50000\n'; }
ensure_chain_shell() { printf 'ensure\n' >> "$BASE/mutations"; }
iptables() { printf '%s\n' "$*" >> "$BASE/mutations"; }
build_chain AL_TEST "$BASE/effective.tsv" 2>"$BASE/error" && exit 9
[ ! -e "$BASE/mutations" ] || exit 10
grep -q 'exceeds 1 generated matches' "$BASE/error" || exit 11
printf 'quota-preflight\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "quota-preflight\n"


def test_apply_deadline_rejects_before_chain_construction(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$BASE/effective.tsv" <<'EOF'
# astrill-lazy-effective-v1
row	1	10	cidr	1.1.1.1/32	direct	any	-	Row	origin	-	-
EOF
printf 'old-resolved\n' > "$RESOLVED"
printf 'old-unresolved\n' > "$UNRESOLVED"
printf '9\n' > "$GENERATED_MATCHES_FILE"
printf '0\n' > "$BASE/date-calls"
date() {
    calls=$(cat "$BASE/date-calls")
    calls=$((calls + 1))
    printf '%s\n' "$calls" > "$BASE/date-calls"
    [ "$calls" -lt 4 ] && printf '0\n' || printf '301\n'
}
hybrid_policy_free_kib() { printf '50000\n'; }
ensure_chain_shell() { printf 'ensure %s\n' "$1" >> "$BASE/mutations"; }
iptables() { printf '%s\n' "$*" >> "$BASE/mutations"; }
build_chain AL_TEST "$BASE/effective.tsv" && exit 9
[ ! -e "$BASE/mutations" ] || exit 10
[ "$(cat "$RESOLVED")" = old-resolved ] || exit 11
[ "$(cat "$UNRESOLVED")" = old-unresolved ] || exit 12
[ "$(cat "$GENERATED_MATCHES_FILE")" = 9 ] || exit 13
[ -z "$(find "$BASE" -maxdepth 1 \
    \( -name 'hybrid-plan.*' -o -name 'resolved.new.*' -o \
       -name 'unresolved.new.*' -o -name 'generated-matches.new.*' \) \
    -print -quit)" ] || exit 14
printf 'deadline-rejected-before-mutation\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "deadline-rejected-before-mutation\n"


def test_core_readback_failure_restores_exact_nvram_runtime_and_chain(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old-row	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old-origin
EOF
cat > "$PREVIOUS" <<'EOF'
# astrill-lazy-rules-v1
older-row	1	10	cidr	9.9.9.9/32	direct	any	-	Older	older-origin
EOF
cat > "$BASE/candidate.tsv" <<'EOF'
# astrill-lazy-rules-v1
new-row	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new-origin
EOF
printf '5\n' > "$CORE_GENERATION_FILE"
NVRAM=$BASE/nvram
mkdir -p "$NVRAM"
cp "$CURRENT" "$NVRAM/$CURRENT_RULES_KEY"
cp "$PREVIOUS" "$NVRAM/$PREVIOUS_RULES_KEY"
nvram() {
    operation=$1
    argument=${2:-}
    key=${argument%%=*}
    case $operation in
        get) [ ! -f "$NVRAM/$argument" ] || cat "$NVRAM/$argument" ;;
        set)
            if [ "$key" = "$CURRENT_RULES_KEY" ] &&
               [ ! -f "$NVRAM/corrupted-once" ]; then
                printf '# truncated' > "$NVRAM/$key"
                : > "$NVRAM/corrupted-once"
            else
                printf '%s' "${argument#*=}" > "$NVRAM/$key"
            fi
            ;;
        unset) rm -f "$NVRAM/$argument" ;;
        commit) return 0 ;;
    esac
}
encode_rule_document() { return 1; }
check_persistence_capacity() { PERSIST_PREVIOUS=1; }
printf 'AL_LAZY_A\n' > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() { printf 'AL_LAZY_B\n' > "$BASE/test-active"; }
activate_chain() {
    printf '%s\n' "$1" > "$BASE/test-active"
    printf 'restore=%s\n' "$1" >> "$BASE/chain-trace"
}
discard_policy_chain() {
    printf 'discard=%s\n' "$1" >> "$BASE/chain-trace"
}
hybrid_apply_core_file "$BASE/candidate.tsv" 5 2>/dev/null && exit 9
grep -q old-origin "$CURRENT" || exit 10
grep -q older-origin "$PREVIOUS" || exit 11
grep -q old-origin "$NVRAM/$CURRENT_RULES_KEY" || exit 12
grep -q older-origin "$NVRAM/$PREVIOUS_RULES_KEY" || exit 13
[ "$(cat "$BASE/test-active")" = AL_LAZY_A ] || exit 14
grep -q 'discard=AL_LAZY_B' "$BASE/chain-trace" || exit 15
[ ! -e "$CORE_RECOVERY_FILE" ] || exit 16
[ -z "$(find "$BASE" -maxdepth 1 -name 'core.tx.*' -print -quit)" ] ||
    exit 17
printf 'readback-restored\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "readback-restored\n"


def test_controller_lock_does_not_steal_an_initializing_owner(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
mkdir "$LOCK_DIR" || exit 8
sleep 4 &
owner_pid=$!
(
    sleep 0.2
    [ -d "$LOCK_DIR" ] || {
        printf 'stolen\n' > "$BASE/race-result"
        exit
    }
    printf '%s\n' "$owner_pid" > "$LOCK_DIR/pid"
    printf 'preserved\n' > "$BASE/race-result"
    sleep 1.2
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR"
    kill "$owner_pid" 2>/dev/null || true
) &
publisher=$!
acquire_lock
wait "$publisher"
[ "$(cat "$BASE/race-result")" = preserved ] || exit 9
[ "$(cat "$LOCK_DIR/pid")" = "$$" ] || exit 10
release_lock
printf 'initializing-owner-preserved\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "initializing-owner-preserved\n"


def test_controller_lock_publish_failure_removes_unowned_directory(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
printf() {
    if [ "$1" = '%s\n' ] && [ "${2:-}" = "$$" ]; then
        return 1
    fi
    command printf "$@"
}
acquire_lock
exit 9
""",
    )

    assert result.returncode == 1
    assert "could not publish controller lock ownership" in result.stderr
    assert not (tmp_path / "runtime" / "controller.lock").exists()


def test_successful_command_exits_nonzero_when_transaction_recovery_fails(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper() { return 1; }
acquire_lock
: > "$POLICY_TRANSACTION_FILE"
exit 0
""",
    )

    assert result.returncode == 1
    assert not (tmp_path / "runtime" / "controller.lock").exists()


def test_chain_activation_removes_old_and_duplicate_jumps_exactly(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
STATE=$BASE/prerouting
cat > "$STATE" <<'EOF'
-A PREROUTING -j AL_LAZY_B
-A PREROUTING -j AL_LAZY_A
-A PREROUTING -j AL_LAZY_B
EOF
iptables() {
    shift 4
    operation=$1
    shift
    case $operation in
        -S)
            printf '%s\n' '-P PREROUTING ACCEPT'
            cat "$STATE"
            ;;
        -I)
            chain=$1
            number=$2
            shift 2
            [ "$chain" = PREROUTING ] && [ "$number" = 1 ] || return 1
            printf '%s\n' "-A PREROUTING $*" > "$STATE.new"
            cat "$STATE" >> "$STATE.new"
            mv "$STATE.new" "$STATE"
            ;;
        -D)
            chain=$1
            shift
            [ "$chain" = PREROUTING ] || return 1
            target=$2
            awk -v target="$target" '
                $0 == "-A PREROUTING -j " target && !removed++ { next }
                { print }
            ' "$STATE" > "$STATE.new"
            mv "$STATE.new" "$STATE"
            ;;
        *) return 1 ;;
    esac
}
activate_chain "$CHAIN_B" || exit 8
[ "$(prerouting_jump_count "$CHAIN_A")" -eq 0 ] || exit 9
[ "$(prerouting_jump_count "$CHAIN_B")" -eq 1 ] || exit 10
[ "$(first_prerouting_rule)" = "-A PREROUTING -j $CHAIN_B" ] || exit 11
[ "$(cat "$ACTIVE_FILE")" = "$CHAIN_B" ] || exit 12
printf 'exact-jump\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "exact-jump\n"


def test_jump_removal_refuses_false_success(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        r"""
iptables() {
    shift 4
    case $1 in
        -S)
            printf '%s\n' '-P PREROUTING ACCEPT'
            printf '%s\n' '-A PREROUTING -j AL_LAZY_A'
            ;;
        -D) return 0 ;;
        *) return 1 ;;
    esac
}
remove_prerouting_jump "$CHAIN_A" && exit 8
activate_chain "$CHAIN_B" && exit 9
[ ! -e "$ACTIVE_FILE" ] || exit 10
printf 'false-success-rejected\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "false-success-rejected\n"


def test_nvram_reserve_counts_serialized_key_overhead(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        r"""
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old
EOF
cat > "$BASE/candidate.tsv" <<'EOF'
# astrill-lazy-rules-v1
new	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new
EOF
load_hybrid_helper || exit 7
encode_rule_document() { return 1; }
raw_value=$(cat "$BASE/candidate.tsv")
raw_bytes=$(printf '%s' "$raw_value" | wc -c)
serialized=$(persistent_rule_bytes \
    "$BASE/candidate.tsv" "$CURRENT_RULES_KEY" "$CURRENT_RULES_GZ_KEY") ||
    exit 8
expected=$((((raw_bytes + ${#CURRENT_RULES_KEY} + 5) / 4) * 4))
[ "$serialized" -eq "$expected" ] || exit 9
[ "$serialized" -gt "$raw_bytes" ] || exit 10
free=$((MIN_NVRAM_FREE_BYTES + serialized - 1))
nvram() {
    case "$1" in
        get) printf '' ;;
        show) printf 'size: 1 bytes (%s left)\n' "$free" >&2 ;;
    esac
}
check_persistence_capacity "$BASE/candidate.tsv" 2>"$BASE/error" && exit 11
grep -q 'insufficient NVRAM headroom' "$BASE/error" || exit 12
printf 'serialized=%s raw=%s\n' "$serialized" "$raw_bytes"
""",
    )

    assert result.returncode == 0, result.stderr
    assert "serialized=" in result.stdout


def test_ensure_runtime_rebuilds_a_chain_with_stale_document_identity(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old
EOF
cat > "$BASE/uncommitted.tsv" <<'EOF'
# astrill-lazy-rules-v1
new	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new
EOF
printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
printf '1\n' > "$CORE_GENERATION_FILE"
printf '%s\n' "$CHAIN_A" > "$ACTIVE_FILE"
document_hash "$BASE/uncommitted.tsv" > "$BASE/chain-a.document-hash"
current_runtime_document() { printf '%s\n' "$CURRENT"; }
runtime_policy_validate() { validate_rules "$1"; }
ensure_routes() { return 0; }
chain_exists() { return 0; }
prerouting_jump_count() {
    [ "$1" = "$CHAIN_A" ] && printf '1\n' || printf '0\n'
}
first_prerouting_rule() { printf '%s\n' "-A PREROUTING -j $CHAIN_A"; }
apply_runtime() {
    cp "$1" "$BASE/rebuilt-from.tsv"
    printf '%s\n' "$CHAIN_B" > "$ACTIVE_FILE"
}
ensure_app_runtime() { return 0; }
ensure_runtime || exit 8
grep -q old "$BASE/rebuilt-from.tsv" || exit 9
grep -q new "$BASE/rebuilt-from.tsv" && exit 10
printf 'stale-chain-rebuilt\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "stale-chain-rebuilt\n"


def test_watchdog_recovery_rolls_back_interrupted_core_after_activation(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
old	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old
EOF
cat > "$PREVIOUS" <<'EOF'
# astrill-lazy-rules-v1
older	1	10	cidr	9.9.9.9/32	direct	any	-	Older	older
EOF
cat > "$EFFECTIVE" <<'EOF'
# astrill-lazy-effective-v1
old	1	10	cidr	1.1.1.1/32	direct	any	-	Old	old	-	-
EOF
cat > "$BASE/new.tsv" <<'EOF'
# astrill-lazy-rules-v1
new	1	10	cidr	2.2.2.2/32	vpn	any	-	New	new
EOF
printf '7\n' > "$CORE_GENERATION_FILE"
printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
NVRAM=$BASE/nvram
mkdir -p "$NVRAM"
cp "$CURRENT" "$NVRAM/$CURRENT_RULES_KEY"
cp "$PREVIOUS" "$NVRAM/$PREVIOUS_RULES_KEY"
nvram() {
    operation=$1
    argument=${2:-}
    key=${argument%%=*}
    case $operation in
        get) [ ! -f "$NVRAM/$argument" ] || cat "$NVRAM/$argument" ;;
        set) printf '%s' "${argument#*=}" > "$NVRAM/$key" ;;
        unset) rm -f "$NVRAM/$argument" ;;
        commit) return 0 ;;
        show) printf 'size: 1 bytes (100000 left)\n' >&2 ;;
    esac
}
tx=$BASE/core.tx.dead
cp "$CURRENT" "$tx.current"
cp "$PREVIOUS" "$tx.previous"
cp "$EFFECTIVE" "$tx.effective"
: > "$tx.effective-present"
hybrid_backup_runtime_state "$tx" || exit 9
hybrid_capture_persistent_state "$tx.nvram" || exit 10
printf '7\n' > "$tx.generation-before"
printf '8\n' > "$tx.generation"
cp "$CURRENT" "$PREVIOUS"
cp "$BASE/new.tsv" "$CURRENT"
cp "$BASE/new.tsv" "$NVRAM/$CURRENT_RULES_KEY"
printf '8\n' > "$CORE_GENERATION_FILE"
printf '%s\n' "$CHAIN_B" > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
inactive_chain() {
    [ "$(active_chain)" = "$CHAIN_A" ] &&
        printf '%s\n' "$CHAIN_B" || printf '%s\n' "$CHAIN_A"
}
activate_chain() { printf '%s\n' "$1" > "$BASE/test-active"; }
discard_policy_chain() { printf '%s\n' "$1" > "$BASE/discarded"; }
hybrid_record_core_transaction \
    "$CHAIN_A" "$CHAIN_B" "$tx" "$BASE/effective.core.dead.tsv" || exit 11
document_hash "$tx.current" > "$BASE/chain-a.document-hash"
ensure_routes() { return 0; }
chain_exists() { return 0; }
prerouting_jump_count() {
    [ "$1" = "$CHAIN_A" ] && printf '1\n' || printf '0\n'
}
first_prerouting_rule() { printf '%s\n' "-A PREROUTING -j $CHAIN_A"; }
ensure_app_runtime() { return 0; }
ensure_runtime || exit 12
grep -q old "$CURRENT" || exit 13
grep -q older "$PREVIOUS" || exit 14
grep -q old "$NVRAM/$CURRENT_RULES_KEY" || exit 15
[ "$(cat "$CORE_GENERATION_FILE")" = 7 ] || exit 16
[ "$(active_chain)" = "$CHAIN_A" ] || exit 17
[ "$(cat "$BASE/discarded")" = "$CHAIN_B" ] || exit 18
[ ! -e "$POLICY_TRANSACTION_FILE" ] || exit 19
[ -z "$(find "$BASE" -maxdepth 1 -name 'core.tx.dead*' -print -quit)" ] ||
    exit 20
printf 'interrupted-core-recovered\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "interrupted-core-recovered\n"


@pytest.mark.parametrize("operation", ["put", "remove"])
def test_watchdog_recovery_rolls_back_interrupted_overlay_after_activation(
    tmp_path: Path,
    operation: str,
) -> None:
    mutation = (
        r"""
cp "$BASE/new.meta" "$meta"
cp "$BASE/new-effective.tsv" "$EFFECTIVE"
rm -f "$BASE/removed-effective.tsv"
record_document=$OVERLAY_DIR/owner.2.tsv
record_meta=$BASE/new.meta
record_effective=$BASE/new-effective.tsv
"""
        if operation == "put"
        else r"""
rm -f "$OVERLAY_DIR/owner.2.tsv" "$BASE/new.meta" "$BASE/new-effective.tsv"
rm -f "$meta"
cp "$BASE/removed-effective.tsv" "$EFFECTIVE"
record_document=-
record_meta=-
record_effective=$BASE/removed-effective.tsv
"""
    )
    result = _run(
        tmp_path,
        rf"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core
EOF
cat > "$OVERLAY_DIR/owner.1.tsv" <<'EOF'
# astrill-lazy-rules-v1
old	1	20	cidr	2.2.2.2/32	vpn	any	-	Old	old
EOF
cat > "$OVERLAY_DIR/owner.2.tsv" <<'EOF'
# astrill-lazy-rules-v1
new	1	20	cidr	3.3.3.3/32	vpn	any	-	New	new
EOF
hybrid_write_meta "$OVERLAY_DIR/owner.meta" 1 \
    192.168.1.50/32 aa:bb:cc:dd:ee:ff \
    "$OVERLAY_DIR/owner.1.tsv" || exit 9
hybrid_compose_effective "$CURRENT" "$EFFECTIVE" || exit 10
hybrid_write_meta "$BASE/new.meta" 2 \
    192.168.1.50/32 aa:bb:cc:dd:ee:ff \
    "$OVERLAY_DIR/owner.2.tsv" || exit 11
hybrid_compose_effective \
    "$CURRENT" "$BASE/new-effective.tsv" owner \
    "$OVERLAY_DIR/owner.2.tsv" \
    192.168.1.50/32 aa:bb:cc:dd:ee:ff || exit 12
hybrid_compose_effective \
    "$CURRENT" "$BASE/removed-effective.tsv" "" "" "" "" owner || exit 13
meta=$OVERLAY_DIR/owner.meta
meta_backup=$OVERLAY_DIR/owner.meta.tx.dead
effective_backup=$BASE/effective.tx.dead
runtime_backup=$BASE/overlay-runtime.tx.dead
cp "$meta" "$meta_backup"
cp "$EFFECTIVE" "$effective_backup"
hybrid_backup_runtime_state "$runtime_backup" || exit 14
old_hash=$(document_hash "$EFFECTIVE")
{mutation}
printf '%s\n' "$CHAIN_B" > "$BASE/test-active"
active_chain() {{ cat "$BASE/test-active"; }}
activate_chain() {{ printf '%s\n' "$1" > "$BASE/test-active"; }}
discard_policy_chain() {{ printf '%s\n' "$1" > "$BASE/discarded"; }}
hybrid_record_overlay_transaction \
    owner "$CHAIN_A" "$CHAIN_B" "$meta" "$meta_backup" \
    "$effective_backup" "$record_document" "$record_meta" \
    "$record_effective" "$runtime_backup" || exit 15
document_hash "$effective_backup" > "$BASE/chain-a.document-hash"
printf 'epoch\n' > "$RUNTIME_EPOCH_FILE"
printf '1\n' > "$CORE_GENERATION_FILE"
ensure_routes() {{ return 0; }}
chain_exists() {{ return 0; }}
prerouting_jump_count() {{
    [ "$1" = "$CHAIN_A" ] && printf '1\n' || printf '0\n'
}}
first_prerouting_rule() {{ printf '%s\n' "-A PREROUTING -j $CHAIN_A"; }}
ensure_app_runtime() {{ return 0; }}
ensure_runtime || exit 16
hybrid_load_meta owner || exit 17
[ "$hybrid_generation" = 1 ] || exit 18
grep -q 2.2.2.2 "$hybrid_document" || exit 19
[ "$(document_hash "$EFFECTIVE")" = "$old_hash" ] || exit 20
[ "$(active_chain)" = "$CHAIN_A" ] || exit 21
[ ! -e "$POLICY_TRANSACTION_FILE" ] || exit 22
[ ! -e "$OVERLAY_DIR/owner.2.tsv" ] || exit 23
[ -z "$(find "$BASE" "$OVERLAY_DIR" -type f \
    \( -name '*.tx.dead*' -o -name '*-effective.tsv' -o -name 'new.meta' \) \
    -print -quit)" ] || exit 24
printf 'interrupted-overlay-{operation}-recovered\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"interrupted-overlay-{operation}-recovered\n"


def test_failed_overlay_stages_leave_no_candidate_or_transaction_files(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
load_hybrid_helper || exit 8
cat > "$CURRENT" <<'EOF'
# astrill-lazy-rules-v1
core	1	10	cidr	1.1.1.1/32	direct	any	-	Core	core
EOF
cat > "$BASE/old.tsv" <<'EOF'
# astrill-lazy-rules-v1
old	1	20	cidr	2.2.2.2/32	vpn	any	-	Old	old
EOF
cat > "$BASE/new.tsv" <<'EOF'
# astrill-lazy-rules-v1
new	1	20	cidr	3.3.3.3/32	vpn	any	-	New	new
EOF
nvram() {
    case "$2" in
        lan_ipaddr) printf '192.168.1.1' ;;
        lan_netmask) printf '255.255.255.0' ;;
        lan_ifname) printf 'br0' ;;
    esac
}
printf '%s\n' "$CHAIN_A" > "$BASE/test-active"
active_chain() { cat "$BASE/test-active"; }
apply_runtime() {
    [ "$(active_chain)" = "$CHAIN_A" ] &&
        printf '%s\n' "$CHAIN_B" > "$BASE/test-active" ||
        printf '%s\n' "$CHAIN_A" > "$BASE/test-active"
}
activate_chain() { printf '%s\n' "$1" > "$BASE/test-active"; }
hybrid_put_overlay owner 0 192.168.1.50 "$BASE/old.tsv" || exit 9

hybrid_validate_effective() { return 1; }
hybrid_put_overlay owner 1 192.168.1.50 "$BASE/new.tsv" 2>/dev/null &&
    exit 10
hybrid_loaded=false
load_hybrid_helper || exit 11

hybrid_write_meta() {
    printf 'partial\n' > "$1"
    return 1
}
hybrid_put_overlay owner 1 192.168.1.50 "$BASE/new.tsv" 2>/dev/null &&
    exit 12
hybrid_loaded=false
load_hybrid_helper || exit 13

cp() {
    if [ "${2:-}" = "$OVERLAY_DIR/owner.meta.tx.$$" ]; then
        command cp "$1" "$2"
        return 1
    fi
    command cp "$@"
}
hybrid_put_overlay owner 1 192.168.1.50 "$BASE/new.tsv" 2>/dev/null &&
    exit 14
cp() { command cp "$@"; }

[ ! -e "$OVERLAY_DIR/owner.2.tsv" ] || exit 15
[ -z "$(find "$BASE" "$OVERLAY_DIR" -type f \
    \( -name '*.new.*' -o -name '*.tx.*' -o \
       -name 'effective.put.*' -o -name 'hybrid-owners.*' -o \
       -name 'overlay-runtime.tx.*' \) -print -quit)" ] || exit 16
printf 'failed-stages-clean\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "failed-stages-clean\n"


def test_exit_trap_cleans_command_candidate_when_no_transaction_exists(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
acquire_lock
: > "$BASE/overlay.candidate.$$.tsv"
exit 17
""",
    )

    assert result.returncode == 17
    runtime = tmp_path / "runtime"
    assert not list(runtime.glob("overlay.candidate.*.tsv"))
    assert not (runtime / "controller.lock").exists()


@pytest.mark.parametrize(
    ("current", "previous", "returncode", "selected"),
    [
        (
            (
                "# astrill-lazy-rules-v1\n"
                "current\t1\t10\tcidr\t1.1.1.1/32\tdirect\tany\t-\tCurrent\tcurrent\n"
            ),
            None,
            0,
            "current",
        ),
        (
            "corrupt",
            (
                "# astrill-lazy-rules-v1\n"
                "previous\t1\t10\tcidr\t9.9.9.9/32\tdirect\tany\t-\tPrevious\tprevious\n"
            ),
            1,
            "previous",
        ),
        (None, None, 0, "empty"),
        ("corrupt", "also corrupt", 1, "quarantine-empty"),
    ],
)
def test_persisted_core_validator_reuses_boot_selection_under_lock(
    tmp_path: Path,
    current: str | None,
    previous: str | None,
    returncode: int,
    selected: str,
) -> None:
    if SHELL is None:
        pytest.skip("POSIX shell is unavailable")
    nvram_dir = tmp_path / "nvram"
    nvram_dir.mkdir()
    if current is not None:
        (nvram_dir / "astrill_lazy_rules").write_text(current, encoding="ascii")
    if previous is not None:
        (nvram_dir / "astrill_lazy_rules_previous").write_text(
            previous,
            encoding="ascii",
        )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nvram = bin_dir / "nvram"
    nvram.write_text(
        "#!/bin/sh\n"
        '[ "$1" = get ] || exit 0\n'
        '[ ! -f "$NVRAM_DIR/$2" ] || cat "$NVRAM_DIR/$2"\n',
        encoding="ascii",
        newline="\n",
    )
    nvram.chmod(0o755)
    runtime = tmp_path / "runtime"
    environment = {
        **os.environ,
        "PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin",
        "NVRAM_DIR": nvram_dir.as_posix(),
        "ASTRILL_LAZY_BASE": runtime.as_posix(),
    }
    result = subprocess.run(
        [SHELL, CONTROLLER.as_posix(), "validate-persisted-core", "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == returncode, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is (returncode == 0)
    assert report["selected"] == selected
    assert not (runtime / "controller.lock").exists()


def test_runtime_package_identity_requires_marker_and_matching_nvram(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
printf '0.2.11\n' > "$VERSION_FILE"
printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' > "$PACKAGE_MD5_FILE"
nvram() {
    [ "$1" = get ] || return 1
    case "$2" in
        astrill_lazy_installed) printf 1 ;;
        astrill_lazy_version) printf '0.2.11' ;;
        astrill_lazy_pkg_md5) printf 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA' ;;
    esac
}
require_package_identity 0.2.11 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ||
    exit 8
printf 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n' > "$PACKAGE_MD5_FILE"
require_package_identity 0.2.11 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    2>/dev/null && exit 9
rm -f "$PACKAGE_MD5_FILE"
require_package_identity 0.2.11 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    2>/dev/null && exit 10
printf 'runtime-identity-checked\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "runtime-identity-checked\n"


def test_runtime_helper_identity_requires_matching_digest_under_lock(
    tmp_path: Path,
) -> None:
    result = _run(
        tmp_path,
        r"""
helper_md5=$(md5sum "$HYBRID_HELPER" | awk '{print $1}') || exit 8
require_hybrid_helper_identity "$helper_md5" 2>/dev/null && exit 9
acquire_lock
require_hybrid_helper_identity "$helper_md5" || exit 10
require_hybrid_helper_identity bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
    2>/dev/null && exit 11
release_lock
printf 'helper-identity-checked\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "helper-identity-checked\n"
