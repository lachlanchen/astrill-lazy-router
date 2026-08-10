from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "router" / "alctl"


def _posix_shell() -> str | None:
    found = shutil.which("sh")
    if found is not None:
        return found
    git_shell = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
    if git_shell.exists():
        return str(git_shell)
    return None


SHELL = _posix_shell()


def _controller_functions() -> str:
    source = CONTROLLER.read_text(encoding="ascii")
    functions, marker, _dispatch = source.partition("\ncommand=${1:-}\n")
    assert marker
    return functions


def _run_scenario(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    if SHELL is None:
        pytest.skip("POSIX shell is unavailable")
    script = tmp_path / "scenario.sh"
    script.write_text(
        _controller_functions() + "\n" + scenario,
        encoding="ascii",
        newline="\n",
    )
    environment = {
        **os.environ,
        "ASTRILL_LAZY_BASE": (tmp_path / "runtime").as_posix(),
    }
    shell_bin = str(Path(SHELL).resolve().parent)
    environment["PATH"] = os.pathsep.join([shell_bin, environment.get("PATH", "")])
    return subprocess.run(
        [SHELL, str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_dynamic_preferences_skip_occupied_pairs_and_obey_floor(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
ip() {
    if [ "$1" = rule ] && [ "$2" = show ]; then
        cat <<'EOF'
0: from all lookup local
27998: from all lookup 200
28000: from all lookup 110
32766: from all lookup main
EOF
        return 0
    fi
    return 1
}
select_owned_prefs 28000
select_owned_prefs 101 && exit 9
exit 0
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "27996\t27997\n"


def test_dynamic_overlay_records_verifies_and_removes_only_owned_rules(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
RULE_STATE=$BASE/ip-rules
cat > "$RULE_STATE" <<'EOF'
0: from all lookup local
28000: from all lookup 110
32766: from all lookup main
EOF
ip() {
    if [ "$1" = rule ] && [ "$2" = show ]; then
        cat "$RULE_STATE"
        return 0
    fi
    if [ "$1" = rule ] && [ "$2" = add ]; then
        printf '%s: from all fwmark %s lookup %s\n' "$4" "$6" "$8" \
            >> "$RULE_STATE"
        return 0
    fi
    if [ "$1" = rule ] && [ "$2" = del ]; then
        awk -v pref="$4:" -v mark="$6" -v table="$8" '
            !($1 == pref && $0 ~ ("fwmark " mark) &&
                $0 ~ ("lookup " table "($|[[:space:]])"))
        ' "$RULE_STATE" > "$RULE_STATE.new"
        mv "$RULE_STATE.new" "$RULE_STATE"
        return 0
    fi
    if [ "$1" = route ] && [ "$2" = flush ] && [ "$3" = cache ]; then
        return 0
    fi
    return 1
}
tunnel_is_up() {
    return 0
}
direct_table_is_ready() {
    return 0
}
vpn_table_is_ready() {
    return 0
}
native_tables_are_ready() {
    return 0
}
install_owned_overlay || exit 8
cat "$RPDB_PREFS_FILE"
owned_overlay_is_valid || exit 9
printf 'valid\n'
remove_owned_ip_rules || exit 10
cat "$RULE_STATE"
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "27998\t27999\t28000\n"
        "valid\n"
        "0: from all lookup local\n"
        "28000: from all lookup 110\n"
        "32766: from all lookup main\n"
    )


def test_route_readiness_normalizes_ddwrt_spacing_and_requires_fallback(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
scenario_vpn_up=true
include_fallback=true
direct_extra=false
nvram() {
    case "$2" in
        wan_iface) printf vlan2 ;;
        wan_gateway) printf 192.168.2.1 ;;
    esac
}
tunnel_is_up() {
    [ "$scenario_vpn_up" = true ]
}
ip() {
    if [ "$1" = route ] && [ "$2" = show ] && [ "$3" = table ]; then
        case "$4" in
            213)
                printf 'default via 192.168.2.1 dev vlan2   \n'
                [ "$direct_extra" = false ] ||
                    printf '192.0.2.0/24 via 192.168.2.1 dev vlan2\n'
                ;;
            212)
                if [ "$scenario_vpn_up" = true ]; then
                    printf 'default via 10.8.0.1 dev tun0  \n'
                fi
                if [ "$include_fallback" = true ]; then
                    printf 'blackhole default  metric 32767   \n'
                fi
                ;;
            main) printf '0.0.0.0/1 via 10.8.0.1 dev tun0   \n' ;;
        esac
        return 0
    fi
    return 1
}
direct_table_is_ready || exit 8
vpn_table_is_ready || exit 9
direct_extra=true
direct_table_is_ready && exit 12
direct_extra=false
include_fallback=false
vpn_table_is_ready && exit 10
scenario_vpn_up=false
include_fallback=true
vpn_table_is_ready || exit 11
printf 'ready\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "ready\n"


def test_vpn_gateway_uses_current_astrill_native_policy_table(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
ip() {
    if [ "$1" = rule ] && [ "$2" = show ]; then
        cat <<'EOF'
0: from all lookup local
32764: from all fwmark 0x1000000/0x3000000 lookup 113
32766: from all lookup main
EOF
        return 0
    fi
    if [ "$1" = route ] && [ "$2" = show ] && [ "$3" = table ]; then
        case "$4" in
            main) printf '198.18.0.0/20 dev tun0 scope link\n' ;;
            113) printf 'default via 198.18.0.1 dev tun0 metric 5\n' ;;
        esac
        return 0
    fi
    return 1
}
[ "$(vpn_gateway_for_tun)" = '198.18.0.1' ] || exit 8
printf 'native-route-ready\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "native-route-ready\n"


def test_app_flow_delete_does_not_depend_on_vpn_route_health(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
cat > "$APP_FLOWS" <<'EOF'
first	192.168.1.240	tcp	1024:65535	vpn
second	192.168.1.241	udp	443	vpn
EOF
ensure_routes() {
    printf 'called\n' > "$BASE/ensure-routes-called"
    return 1
}
build_app_chain() {
    cp "$1" "$BASE/built-app-chain"
}
ensure_app_jump() {
    return 0
}
cleanup_app_chain() {
    return 0
}
transform_app_flow delete first || exit 8
[ ! -f "$BASE/ensure-routes-called" ] || exit 9
grep -q '^second' "$APP_FLOWS" || exit 10
grep -q '^first' "$APP_FLOWS" && exit 11
printf 'delete-ready\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "delete-ready\n"


def test_unmanaged_native_undercut_sets_marker_without_priority_walk(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
RULE_STATE=$BASE/ip-rules
ADD_COUNT=$BASE/add-count
cat > "$RULE_STATE" <<'EOF'
0: from all lookup local
27997: from all lookup 110
27998: from all fwmark 0x4000000/0xc000000 lookup 213
27999: from all fwmark 0x8000000/0xc000000 lookup 212
32766: from all lookup main
EOF
printf '0\n' > "$ADD_COUNT"
printf '27998\t27999\t28000\n' > "$RPDB_PREFS_FILE"
ip() {
    if [ "$1" = rule ] && [ "$2" = show ]; then
        cat "$RULE_STATE"
        return 0
    fi
    if [ "$1" = rule ] && [ "$2" = add ]; then
        count=$(cat "$ADD_COUNT")
        printf '%s\n' $((count + 1)) > "$ADD_COUNT"
        return 0
    fi
    if [ "$1" = rule ] && [ "$2" = del ]; then
        awk -v pref="$4:" -v mark="$6" -v table="$8" '
            !($1 == pref && $0 ~ ("fwmark " mark) &&
                $0 ~ ("lookup " table "($|[[:space:]])"))
        ' "$RULE_STATE" > "$RULE_STATE.new"
        mv "$RULE_STATE.new" "$RULE_STATE"
        return 0
    fi
    if [ "$1" = route ] && [ "$2" = flush ]; then
        return 0
    fi
    return 1
}
tunnel_is_up() {
    [ "${scenario_down:-false}" != true ]
}
ensure_vpn_fail_closed() {
    return 0
}
ensure_policy_tables() {
    return 0
}
ensure_routes && exit 8
ensure_routes && exit 9
printf 'adds=%s owned=%s marker=%s\n' \
    "$(cat "$ADD_COUNT")" "$(owned_rule_count)" "$(rebase_is_required && printf yes)"
cat "$RULE_STATE"
scenario_down=true
ensure_routes || exit 10
printf 'down-marker=%s\n' "$(rebase_is_required && printf yes || printf no)"
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "adds=0 owned=0 marker=yes\n"
        "0: from all lookup local\n"
        "27997: from all lookup 110\n"
        "32766: from all lookup main\n"
        "down-marker=no\n"
    )


@pytest.mark.parametrize(
    ("vpn_up", "expected"),
    [
        (
            True,
            {
                "health": "degraded",
                "policy_health": "degraded",
                "precedence_ok": False,
                "native_min_pref": 27998,
                "direct_pref": 27996,
                "vpn_pref": 27997,
                "table_readiness": {
                    "direct": True,
                    "vpn": True,
                    "native": True,
                },
                "vpn_fail_closed": True,
                "last_reconcile_error": (
                    "could not install companion rules ahead of Astrill native policy"
                ),
            },
        ),
        (
            False,
            {
                "health": "healthy",
                "policy_health": "ready",
                "precedence_ok": True,
                "native_min_pref": None,
                "direct_pref": None,
                "vpn_pref": None,
                "table_readiness": {
                    "direct": True,
                    "vpn": True,
                    "native": False,
                },
                "vpn_fail_closed": True,
                "last_reconcile_error": None,
            },
        ),
    ],
)
def test_status_reports_connected_degradation_and_safe_down_state(
    tmp_path: Path,
    vpn_up: bool,
    expected: dict[str, object],
) -> None:
    up_value = "true" if vpn_up else "false"
    result = _run_scenario(
        tmp_path,
        rf"""
scenario_vpn_up={up_value}
printf '0.2.test\n' > "$VERSION_FILE"
printf 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n' > "$PACKAGE_MD5_FILE"
initialize_rules() {{
    printf '# astrill-lazy-rules-v1\n' > "$CURRENT"
}}
active_chain() {{
    printf '%s\n' "$CHAIN_A"
}}
nvram() {{
        case "${{2:-}}" in
            astrill_status) [ "$scenario_vpn_up" = true ] && printf 3 || printf 0 ;;
            astrill_serverid|astrill_protocol) printf 1 ;;
            astrill_lazy_installed) printf 1 ;;
            astrill_lazy_version) printf '0.2.test' ;;
            astrill_lazy_pkg_md5) printf aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
            wan_iface) printf vlan2 ;;
        wan_gateway) printf 192.168.2.1 ;;
        *) printf '' ;;
    esac
}}
tunnel_is_up() {{
    [ "$scenario_vpn_up" = true ]
}}
watchdog_running() {{
    return 0
}}
iptables() {{
    return 0
}}
direct_table_is_ready() {{
    return 0
}}
vpn_table_is_ready() {{
    return 0
}}
native_tables_are_ready() {{
    [ "$scenario_vpn_up" = true ]
}}
vpn_fail_closed_is_ready() {{
    return 0
}}
native_min_pref() {{
    [ "$scenario_vpn_up" = true ] && printf '27998\n'
}}
current_owned_pref() {{
    [ "$scenario_vpn_up" = true ] || return 0
    [ "$2" = "$DIRECT_TABLE" ] && printf '27996\n' || printf '27997\n'
}}
owned_overlay_is_valid() {{
    return 1
}}
owned_rule_count() {{
    [ "$scenario_vpn_up" = true ] && printf '2\n' || printf '0\n'
}}
if [ "$scenario_vpn_up" = true ]; then
    printf '%s\n' \
        'could not install companion rules ahead of Astrill native policy' \
        > "$RECONCILE_ERROR_FILE"
fi
status_json
""",
    )

    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["ok"] is True
    assert status["vpn_state"] == ("up" if vpn_up else "down")
    for key, value in expected.items():
        assert status[key] == value


def test_watchdog_uses_a_nonblocking_controller_lock() -> None:
    controller = CONTROLLER.read_text(encoding="ascii")
    assert 'mkdir "$LOCK_DIR" 2>/dev/null || return 1' in controller
    assert "if try_watchdog_lock; then" in controller
    assert '"$0" ensure' not in controller
    assert '"$0" refresh' not in controller
    assert "release_watchdog_lock" in controller


def test_watchdog_reclaims_dead_and_missing_pid_locks(tmp_path: Path) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
sleep() {
    return 0
}
mkdir "$LOCK_DIR"
printf '99999999\n' > "$LOCK_DIR/pid"
try_watchdog_lock || exit 8
[ "$(cat "$LOCK_DIR/pid")" = "$$" ] || exit 9
release_watchdog_lock
mkdir "$LOCK_DIR"
try_watchdog_lock || exit 10
[ "$(cat "$LOCK_DIR/pid")" = "$$" ] || exit 11
release_watchdog_lock
mkdir "$LOCK_DIR"
printf '%s\n' "$$" > "$LOCK_DIR/pid"
try_watchdog_lock && exit 12
[ "$(cat "$LOCK_DIR/pid")" = "$$" ] || exit 13
rm -f "$LOCK_DIR/pid"
rmdir "$LOCK_DIR"
printf 'reclaimed\n'
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "reclaimed\n"


def test_cleanup_is_best_effort_and_propagates_owned_runtime_failures(
    tmp_path: Path,
) -> None:
    result = _run_scenario(
        tmp_path,
        r"""
TRACE=$BASE/cleanup-trace
: > "$TRACE"
remove_prerouting_jump() {
    printf 'jump-%s\n' "$1" >> "$TRACE"
    return 0
}
chain_exists() {
    return 1
}
remove_vpn_fail_closed_jump() {
    printf 'filter-jump\n' >> "$TRACE"
    return 1
}
filter_chain_exists() {
    return 1
}
rule_prefs_for_signature() {
    [ "$2" = "$DIRECT_TABLE" ] && printf '123\n' || printf '124\n'
}
remove_exact_ip_rule() {
    printf 'rule-%s-%s\n' "$1" "$3" >> "$TRACE"
    [ "$3" != "$DIRECT_TABLE" ]
}
owned_rule_count() {
    printf '1\n'
}
iptables() {
    return 1
}
ip() {
    if [ "$1" = route ] && [ "$2" = flush ]; then
        printf 'flush-%s\n' "${4:-cache}" >> "$TRACE"
        return 0
    fi
    if [ "$1" = route ] && [ "$2" = show ]; then
        return 0
    fi
    return 1
}
cleanup_policy && exit 8
cat "$TRACE"
""",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "jump-AL_LAZY_APP\n"
        "jump-AL_LAZY_A\n"
        "jump-AL_LAZY_B\n"
        "filter-jump\n"
        "rule-123-213\n"
        "rule-124-212\n"
        "flush-213\n"
        "flush-212\n"
        "flush-cache\n"
    )
