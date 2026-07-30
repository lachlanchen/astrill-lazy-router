from __future__ import annotations

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
    }
    return subprocess.run(
        [SHELL, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


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
active_chain() { printf 'AL_LAZY_A\n'; }
apply_runtime() { cp "$1" "$BASE/applied.tsv"; }
activate_chain() { return 0; }
hybrid_put_overlay first 0 auto "$BASE/shared.tsv" || exit 9
hybrid_load_meta first || exit 10
printf 'generation=%s source=%s mac=%s\n' \
    "$hybrid_generation" "$hybrid_source" "$hybrid_mac"
hybrid_put_overlay first 0 auto "$BASE/shared.tsv" 2>"$BASE/stale" &&
    exit 11
hybrid_put_overlay second 0 192.168.1.50 "$BASE/shared.tsv" \
    2>/dev/null && exit 12
hybrid_put_overlay second 0 192.168.1.60 "$BASE/collision.tsv" \
    2>/dev/null && exit 13
[ ! -s "$NVRAM_WRITES" ] || exit 14
cat "$BASE/stale"
printf 'overlays=%s no-nvram-writes\n' "$(hybrid_overlay_count)"
""",
    )

    assert result.returncode == 0, result.stderr
    assert (
        "generation=1 source=192.168.1.50/32 "
        "mac=aa:bb:cc:dd:ee:ff"
    ) in result.stdout
    assert "overlay generation conflict: expected 0, current 1" in result.stdout
    assert result.stdout.endswith("overlays=1 no-nvram-writes\n")


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
encode_rule_document() { printf 'GZ'; }
active_chain() { printf 'AL_LAZY_A\n'; }
apply_runtime() { return 0; }
activate_chain() { return 0; }
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
check_persistence_capacity() { return 0; }
active_chain() { printf 'AL_LAZY_A\n'; }
apply_runtime() { return 0; }
persist_rules() { return 1; }
activate_chain() { printf '%s\n' "$1" > "$BASE/restored"; }
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
active_chain() { printf 'AL_LAZY_A\n'; }
apply_runtime() { return 0; }
activate_chain() { printf '%s\n' "$1" > "$BASE/restored-chain"; }
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
[ "$(cat "$BASE/restored-chain")" = AL_LAZY_A ] || exit 14
grep -q 2.2.2.2 "$hybrid_document" || exit 15
printf 'overlay-restored\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "overlay-restored\n"


def test_hybrid_helper_is_not_part_of_nvram_boot_package() -> None:
    installer = (
        ROOT / "desktop" / "astrill_lazy" / "installer.py"
    ).read_text(encoding="utf-8")
    assert '"alhybrid"' not in installer.partition("PACKAGE_FILES =")[2].partition(
        ")"
    )[0]
