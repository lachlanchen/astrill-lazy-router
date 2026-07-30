from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from astrill_lazy.router import _clean_ssh_stderr, is_ssh_authentication_failure

ROOT = Path(__file__).resolve().parents[1]


def _shell() -> str | None:
    found = shutil.which("sh")
    if found is not None:
        return found
    candidate = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
    return str(candidate) if candidate.exists() else None


SHELL = _shell()


@pytest.mark.skipif(SHELL is None, reason="POSIX shell is unavailable")
def test_router_and_helper_scripts_parse_with_posix_shell() -> None:
    assert SHELL is not None
    scripts = [
        ROOT / "router" / "alctl",
        ROOT / "router" / "alapi",
        ROOT / "router" / "alpage",
        ROOT / "router" / "alpage-ui",
        ROOT / "router" / "bootstrap.sh",
        ROOT / "helpers" / "astrill-lazy-netns",
        ROOT / "helpers" / "astrill-lazy-profile-runner",
        ROOT / "scripts" / "install-desktop.sh",
        ROOT / "scripts" / "install-novnc-service.sh",
        ROOT / "scripts" / "run-novnc-debug.sh",
        ROOT / "scripts" / "uninstall-novnc-service.sh",
        ROOT / "contrib" / "macos" / "install-launcher.sh",
        ROOT / "contrib" / "macos" / "install-uuremote-route-reporter.sh",
        ROOT / "contrib" / "macos" / "uuremote-route-reporter.sh",
        ROOT / "contrib" / "portable" / "install-agent.sh",
        ROOT / "contrib" / "portable" / "uninstall-agent.sh",
    ]
    for script in scripts:
        subprocess.run([SHELL, "-n", script.as_posix()], check=True)


def test_router_page_is_layered_read_only_and_refreshes_only_on_request() -> None:
    fallback = (ROOT / "router" / "alpage").read_text(encoding="ascii")
    page = (ROOT / "router" / "alpage-ui").read_text(encoding="ascii")

    assert "p=/tmp/astrill-lazy/alpage-ui" in fallback
    assert 'exec "$p"' in fallback
    assert "href=/MyPage.asp?4" in fallback
    assert "Persistent core" in page
    assert "RAM overlays" in page
    assert "Effective policy" in page
    assert "x.owner" in page
    assert "x.source" in page
    assert "x.mac" in page
    assert "x.hash" in page
    assert "fetch('/MyPage.asp?4'" in page
    assert "$('refresh').onclick=load;load();" in page
    assert "Read-only. No polling." in page
    assert "setInterval" not in page
    assert "setTimeout" not in page
    assert "/apply.cgi" not in page
    assert "nvram " not in page


def test_router_bootstrap_holds_lock_through_atomic_replacement() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    installer = (ROOT / "desktop" / "astrill_lazy" / "installer.py").read_text(
        encoding="utf-8"
    )

    stop_watchdog = bootstrap.index("for pid in $(watchdog_pids)")
    acquire_lock = bootstrap.index('while ! mkdir "$LOCK"')
    locked_digest = bootstrap.index(
        'ACTUAL=$(md5sum "$ARCHIVE"',
        acquire_lock,
    )
    extract_package = bootstrap.index('tar -xzf "$ARCHIVE" -C "$STAGE"')
    replace = bootstrap.index('mv "$BASE/$name.new.$$" "$BASE/$name"')
    marker = bootstrap.index('mv "$BASE/PACKAGE_MD5.new.$$" "$BASE/PACKAGE_MD5"')
    release = bootstrap.index('rmdir "$LOCK" || exit 1')
    start = bootstrap.index('"$BASE/alctl" start')
    lock_publish = bootstrap.index('printf \'%s\\n\' "$$" > "$LOCK/pid"')
    watchdog_stops = [
        match.start()
        for match in re.finditer(
            r"^stop_watchdogs \|\| exit 1$", bootstrap, re.MULTILINE
        )
    ]
    assert (
        stop_watchdog
        < acquire_lock
        < locked_digest
        < extract_package
        < replace
        < marker
        < release
        < start
    )
    assert len(watchdog_stops) == 2
    assert watchdog_stops[0] < acquire_lock < lock_publish < watchdog_stops[1]
    assert 'kill -0 "$lock_pid"' in bootstrap
    assert '*" $BASE/alctl refresh "*)' in bootstrap
    assert 'kill -9 "$lock_pid"' in bootstrap
    assert 'printf \'%s\\n\' "$$" > "$LOCK/pid"' in bootstrap
    assert "timeout=300" in installer


def test_router_bootstrap_gives_initializing_lock_owner_a_grace_period() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    lock_loop = bootstrap[
        bootstrap.index('while ! mkdir "$LOCK"') : bootstrap.index("locked=true")
    ]
    pid_read = 'lock_pid=$(cat "$LOCK/pid"'
    assert lock_loop.count(pid_read) == 2
    first_read = lock_loop.index(pid_read)
    grace = lock_loop.index("sleep 1", first_read)
    second_read = lock_loop.index(pid_read, first_read + 1)
    reclaim = lock_loop.index('rm -f "$LOCK/pid"')
    assert first_read < grace < second_read < reclaim


def test_router_bootstrap_uses_process_scoped_prelock_artifacts() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")

    assert "ARCHIVE=/tmp/astrill-lazy-router.$$.tar.gz" in bootstrap
    assert "ENCODED=/tmp/astrill-lazy-router.$$.b64" in bootstrap
    assert "ARCHIVE=/tmp/astrill-lazy-router.tar.gz" not in bootstrap
    assert "ENCODED=/tmp/astrill-lazy-router.b64" not in bootstrap


def test_router_bootstrap_refuses_to_discard_pending_policy_recovery() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    lock_publish = bootstrap.index('printf \'%s\\n\' "$$" > "$LOCK/pid"')
    journal_guard = bootstrap.index(
        '[ ! -f "$BASE/policy-transaction" ]',
        lock_publish,
    )
    replacement = bootstrap.index('rm -f "$BASE/PACKAGE_MD5"')
    assert lock_publish < journal_guard < replacement
    assert 'rm -f "$BASE/alhybrid" "$BASE/policy-transaction"' not in bootstrap


def test_router_bootstrap_rechecks_captured_identity_under_lock() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    assert "${ASTRILL_LAZY_BOOTSTRAP_MD5:-}" in bootstrap
    assert "RECOVERY=${ASTRILL_LAZY_RECOVERY:-0}" in bootstrap
    assert "${ASTRILL_LAZY_RECOVERY_VERSION:-}" in bootstrap
    assert "${ASTRILL_LAZY_RECOVERY_PACKAGE_MD5:-}" in bootstrap
    assert "${ASTRILL_LAZY_RECOVERY_BOOTSTRAP_MD5:-}" in bootstrap
    assert '[ "$(nvram get astrill_lazy_installed)" = 1 ]' in bootstrap
    assert '[ "$(nvram get astrill_lazy_version)" = "$RECOVERY_VERSION" ]' in (
        bootstrap
    )
    check = "verify_bootstrap_identity || exit 1"
    assert bootstrap.count(check) == 2
    first_check = bootstrap.index(check)
    second_check = bootstrap.index(check, first_check + 1)
    package_read = bootstrap.index(
        "COUNT=$(nvram get astrill_lazy_pkg_count)",
    )
    lock_publish = bootstrap.index('printf \'%s\\n\' "$$" > "$LOCK/pid"')
    replacement = bootstrap.index('rm -f "$BASE/PACKAGE_MD5"')
    assert first_check < package_read < lock_publish < second_check < replacement


@pytest.mark.skipif(SHELL is None, reason="POSIX shell is unavailable")
def test_bootstrap_publishes_verified_runtime_marker_and_resets_ram_layers(
    tmp_path: Path,
) -> None:
    assert SHELL is not None
    base = tmp_path / "runtime"
    overlays = base / "overlays"
    overlays.mkdir(parents=True)
    (base / "alhybrid").write_text("old helper\n", encoding="ascii")
    (base / "rules.tsv").write_text("old core\n", encoding="ascii")
    (base / "runtime-epoch").write_text("old epoch\n", encoding="ascii")
    (base / "chain-a.document-hash").write_text("old hash\n", encoding="ascii")
    (base / "active-chain").write_text("AL_LAZY_A\n", encoding="ascii")
    (overlays / "owner.meta").write_text("old overlay\n", encoding="ascii")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "nvram": """#!/bin/sh
[ "$1" = get ] || exit 1
case "$2" in
  astrill_lazy_bootstrap) printf '%s' "$BOOTSTRAP_TEXT" ;;
  astrill_lazy_bootstrap_md5) printf '%s' "$BOOTSTRAP_DIGEST" ;;
  astrill_lazy_pkg_count) printf 1 ;;
  astrill_lazy_pkg_md5) printf '%s' "$PACKAGE_DIGEST" ;;
  astrill_lazy_pkg_0) printf X ;;
  astrill_lazy_installed) printf 1 ;;
  astrill_lazy_version) printf '%s' "$PACKAGE_VERSION" ;;
  *) printf '' ;;
esac
""",
        "uudecode": """#!/bin/sh
while [ "$#" -gt 0 ]; do
  [ "$1" != -o ] || { shift; output=$1; }
  shift
done
cp "$ARCHIVE_SOURCE" "$output"
""",
        "md5sum": """#!/bin/sh
printf 'call\n' >> "$MD5_TRACE"
if [ "$1" = "$BOOTSTRAP_COPY_PATH" ]; then
  printf '%s  %s\n' "$BOOTSTRAP_DIGEST" "$1"
else
  printf '%s  %s\n' "$PACKAGE_DIGEST" "$1"
fi
""",
        "tar": """#!/bin/sh
while [ "$#" -gt 0 ]; do
  [ "$1" != -C ] || { shift; target=$1; }
  shift
done
mkdir -p "$target/astrill-lazy"
cat > "$target/astrill-lazy/alctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$1" > "$START_RECORD"
EOF
printf '#!/bin/sh\n' > "$target/astrill-lazy/alapi"
printf '#!/bin/sh\n' > "$target/astrill-lazy/alpage"
printf '%s\n' "$PACKAGE_VERSION" > "$target/astrill-lazy/VERSION"
""",
    }
    for name, script in scripts.items():
        path = bin_dir / name
        path.write_text(script, encoding="ascii", newline="\n")
        path.chmod(0o755)

    archive = tmp_path / "package.tar.gz"
    archive.write_bytes(b"verified package bytes")
    source = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    replacements = {
        "BASE=/tmp/astrill-lazy": f"BASE='{base.as_posix()}'",
        "ARCHIVE=/tmp/astrill-lazy-router.$$.tar.gz": (
            f"ARCHIVE='{(tmp_path / 'decoded.tar.gz').as_posix()}'"
        ),
        "ENCODED=/tmp/astrill-lazy-router.$$.b64": (
            f"ENCODED='{(tmp_path / 'encoded.b64').as_posix()}'"
        ),
        "BOOTSTRAP_COPY=/tmp/astrill-lazy-bootstrap.$$.sh": (
            f"BOOTSTRAP_COPY='{(tmp_path / 'bootstrap-copy.sh').as_posix()}'"
        ),
        "STAGE=/tmp/astrill-lazy-install.$$": (
            f"STAGE='{(tmp_path / 'stage').as_posix()}.$$'"
        ),
    }
    for before, after in replacements.items():
        source = source.replace(before, after)
    scenario = tmp_path / "bootstrap.sh"
    scenario.write_text(source, encoding="ascii", newline="\n")
    digest = "a" * 32
    bootstrap_digest = "b" * 32
    version = "0.2.11"
    environment = {
        **os.environ,
        "PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin",
        "ARCHIVE_SOURCE": archive.as_posix(),
        "PACKAGE_DIGEST": digest,
        "BOOTSTRAP_DIGEST": bootstrap_digest,
        "ASTRILL_LAZY_BOOTSTRAP_MD5": bootstrap_digest,
        "BOOTSTRAP_TEXT": "#!/bin/sh\nprintf bootstrap\n",
        "BOOTSTRAP_COPY_PATH": (tmp_path / "bootstrap-copy.sh").as_posix(),
        "PACKAGE_VERSION": version,
        "MD5_TRACE": (tmp_path / "md5.trace").as_posix(),
        "START_RECORD": (tmp_path / "start.record").as_posix(),
    }
    result = subprocess.run(
        [SHELL, scenario.as_posix()],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert (base / "PACKAGE_MD5").read_text(encoding="ascii").strip() == digest
    assert (base / "VERSION").read_text(encoding="ascii").strip() == version
    assert (tmp_path / "md5.trace").read_text(encoding="ascii").count("call") == 4
    assert (tmp_path / "start.record").read_text(encoding="ascii").strip() == "start"
    assert (base / "active-chain").read_text(encoding="ascii").strip() == "AL_LAZY_A"
    assert not (base / "alhybrid").exists()
    assert not (base / "rules.tsv").exists()
    assert not (base / "runtime-epoch").exists()
    assert not (base / "chain-a.document-hash").exists()
    assert list((base / "overlays").iterdir()) == []
    assert not (base / "controller.lock").exists()


@pytest.mark.skipif(SHELL is None, reason="POSIX shell is unavailable")
def test_application_profile_has_a_stable_locally_administered_mac() -> None:
    assert SHELL is not None
    helper = ROOT / "helpers" / "astrill-lazy-netns"
    helper_source = helper.read_text(encoding="ascii")
    result = subprocess.run(
        [SHELL, helper.as_posix(), "identity", "uuremote"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "profile": "uuremote",
        "mac": "02:41:4c:de:39:3a",
    }
    invalid = subprocess.run(
        [SHELL, helper.as_posix(), "identity", "../invalid"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
    assert "invalid profile" in invalid.stderr
    assert "deconfig|bound|renew) dhcp_hook" in helper_source
    assert "dhcp_pid_for" in helper_source
    assert 'grep -Fxq "$pid_file"' in helper_source


def test_application_profile_system_service_is_fixed_and_restartable() -> None:
    unit = (
        ROOT
        / "data"
        / "io.github.lachlanchen.AstrillLazyRouter.ApplicationProfile@.service"
    ).read_text(encoding="ascii")
    runner = (ROOT / "helpers" / "astrill-lazy-profile-runner").read_text(
        encoding="ascii"
    )

    assert "EnvironmentFile=/etc/astrill-lazy/profiles/%i.conf" in unit
    assert "ExecStopPost=-/usr/local/libexec/astrill-lazy-netns cleanup" in unit
    assert "Restart=always" in unit
    assert "pgrep -u" in runner
    assert "DBUS_SESSION_BUS_ADDRESS" in runner
    assert "eval " not in runner


def test_novnc_service_is_rendered_from_the_checked_out_repository() -> None:
    unit = (
        ROOT / "data" / "io.github.lachlanchen.AstrillLazyRouter.NoVNC.service"
    ).read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install-novnc-service.sh").read_text(
        encoding="utf-8"
    )

    assert "@ROOT@" in unit
    assert "Projects/astrill-lazy" not in unit
    assert "EnvironmentFile=-%h/.config/astrill-lazy/novnc.env" in unit
    assert "Restart=always" in unit
    assert 'sed "s|@ROOT@|$escaped_root|g"' in installer
    assert "--install-only" in installer
    assert "DBUS_SESSION_BUS_ADDRESS" in installer

    runner = (ROOT / "scripts" / "run-novnc-debug.sh").read_text(encoding="utf-8")
    for component in ("Xvfb", "openbox", "x11vnc", "websockify", "Astrill Lazy GUI"):
        assert f':{component}"' in runner
    assert "restarting the noVNC stack" in runner


def test_router_upgrade_stages_files_and_forces_fresh_core_reconciliation() -> None:
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    assert 'tar -xzf "$ARCHIVE" -C "$STAGE"' in bootstrap
    assert 'tar -xzf "$ARCHIVE" -C /tmp' not in bootstrap
    assert '"$BASE/alctl" stop' not in bootstrap
    assert 'cp "$SOURCE/$name" "$BASE/$name.new.$$"' in bootstrap
    assert 'mv "$BASE/$name.new.$$" "$BASE/$name"' in bootstrap
    assert '"$BASE/alhybrid"' in bootstrap
    assert '"$BASE/rules.tsv"' in bootstrap
    assert '"$BASE/runtime-epoch"' in bootstrap
    assert '"$BASE/chain-a.document-hash"' in bootstrap
    assert '"$BASE/active-chain"' not in bootstrap


def test_desktop_installer_selects_a_supported_python_and_opt_in_autostart() -> None:
    installer = (ROOT / "scripts" / "install-desktop.sh").read_text(encoding="utf-8")

    assert "python3.12 python3.11 python3" in installer
    assert "sys.version_info < (3, 11)" in installer
    assert "ASTRILL_LAZY_PYTHON" in installer
    assert "ASTRILL_LAZY_ENABLE_AUTOSTART" in installer


def test_remote_novnc_launchers_prefer_the_current_ubuntu_host() -> None:
    macos = (ROOT / "contrib" / "macos" / "open-astrill-lazy.applescript").read_text(
        encoding="utf-8"
    )
    windows = (ROOT / "contrib" / "windows" / "Open-AstrillLazyRouter.ps1").read_text(
        encoding="utf-8"
    )

    assert '"glassagent-ubuntu"' in macos
    for launcher in (macos, windows):
        assert "lachlan@192.168.1.100" in launcher
        assert "127.0.0.1:" in launcher
        assert "BatchMode=yes" in launcher
        assert "password" not in launcher.casefold()


def test_policy_controller_never_evaluates_rule_content() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    helper = (ROOT / "helpers" / "astrill-lazy-netns").read_text(encoding="ascii")
    runner = (ROOT / "helpers" / "astrill-lazy-profile-runner").read_text(
        encoding="ascii"
    )
    assert "eval " not in controller
    assert "eval " not in helper
    assert "eval " not in runner
    assert "ASTRILL_LAZY_PROFILE_DNS" in helper
    assert "write_profile_resolver" in helper
    assert "profile DNS supports at most three servers" in helper
    assert "APP_CHAIN=AL_LAZY_APP" in controller
    assert "MAX_APP_FLOWS=16" in controller
    assert "--sport" in controller
    assert "app-flow)" in controller
    assert "astrill_lazy_app_flows" not in controller
    assert "--set-xmark" in controller
    assert "0xc000000" in controller
    assert "RPDB_PREF_FLOOR=100" in controller
    assert "VPN_BLACKHOLE_METRIC=32767" in controller
    assert "RPDB_RECONCILE_ATTEMPTS" not in controller
    assert "ASTRILL_NATIVE_STABLE_ATTEMPTS=12" in controller
    assert "ASTRILL_NATIVE_STABLE_SAMPLES=2" in controller
    assert "select_owned_prefs" in controller
    assert "wait_for_native_rules_stable" in controller
    assert "record_owned_prefs" in controller
    assert "remove_exact_ip_rule" in controller
    assert "DIRECT_PREF=" not in controller
    assert "VPN_PREF=" not in controller
    assert "WATCHDOG_INTERVAL=60" in controller
    assert "WATCHDOG_REFRESH_CYCLES=30" in controller
    assert "ASTRILL_CONNECT_ATTEMPTS=60" in controller
    assert "MAX_RULE_BYTES=6144" in controller
    assert "MIN_NVRAM_FREE_BYTES=2048" in controller
    assert "CURRENT_RULES_GZ_KEY=astrill_lazy_rules_gz" in controller
    hybrid = (ROOT / "router" / "alhybrid").read_text(encoding="ascii")
    assert 'nvram unset "$PREVIOUS_RULES_KEY"' in hybrid
    assert "persistent_rule_bytes" in hybrid
    assert "persist_rule_document" in hybrid
    assert '"rollback_persistent":%s' in controller
    assert controller.count("iptables -w 10") >= 15
    assert "insufficient NVRAM headroom" in hybrid
    assert "watchdog_pids | grep -qx" in controller
    assert "cleanup_watchdog_pid" in controller
    assert "refresh_mode=${3:-0}" in controller
    assert 'apply_runtime "$refresh_document" 1' in controller
    assert '[ "$(hybrid_overlay_count)" -gt 0 ]' in controller
    assert '"$RESOLVED"' in controller
    assert "HYBRID_DNS_BATCH=8" in hybrid
    assert "iptables-restore" in hybrid
    assert '"$IPTABLES_RESTORE" -w 10 -n -t' in hybrid
    assert 'kill -9 "$pid"' in controller
    assert 'wait "$watchdog_sleep_pid"' in controller
    assert '[ "$watchdog" = true ] || health=degraded' in controller
    assert '"policy_health":"%s","precedence_ok":%s' in controller
    assert '"native_min_pref":%s,"direct_pref":%s,"vpn_pref":%s' in controller
    assert '"table_readiness":{"direct":%s,"vpn":%s,"native":%s}' in controller
    assert '"last_reconcile_error":null' in controller
    assert '"rebase_required":%s' in controller
    assert "VPN_FAIL_CHAIN=AL_LAZY_VPN_FAIL" in controller
    assert '-m mark --mark "$VPN_MARK/$MARK_MASK" -j DROP' in controller
    assert "astrill-connect)" in controller
    assert "astrill-disconnect)" in controller
    assert "/dev/astrill/astrillvpn stop" in controller


def test_policy_mutations_verify_package_and_helper_identity_under_lock() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    mutations = {
        "apply": "initialize_rules",
        "core-apply": "hybrid_apply_core_file",
        "rollback": "initialize_rules",
        "core-rollback": "hybrid_apply_core_file",
        "overlay-put": "hybrid_put_overlay",
        "overlay-remove": "hybrid_remove_overlay",
        "toggle-origin": "transform_origin toggle",
        "route-origin": "transform_origin route",
    }
    for command, mutation in mutations.items():
        match = re.search(
            rf"\n    {re.escape(command)}\)\n"
            r"(?P<body>.*?)(?=\n    [a-z][a-z0-9|-]*\)\n)",
            controller,
            re.DOTALL,
        )
        assert match is not None, command
        body = match.group("body")
        lock = body.index("acquire_lock")
        identity = body.index(
            'require_package_identity "$expected_version" "$expected_md5"'
        )
        helper_identity = body.index(
            'require_hybrid_helper_identity "$expected_helper_md5"'
        )
        write = body.index(mutation)
        assert lock < identity < helper_identity < write

    assert (
        "overlay-put VERSION PACKAGE_MD5 HELPER_MD5 OWNER GENERATION SOURCE "
        "EXPECTED_SOURCE EXPECTED_MAC FILE|-"
    ) in controller
    assert "core-apply VERSION PACKAGE_MD5 HELPER_MD5 GENERATION FILE|-" in controller


def test_router_lock_and_watchdog_traps_include_hup() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    bootstrap = (ROOT / "router" / "bootstrap.sh").read_text(encoding="ascii")
    assert "trap 'exit 129' HUP" in controller
    assert "trap - EXIT HUP INT TERM" in controller
    assert "HUP INT TERM EXIT" in controller
    assert "trap 'exit 129' HUP" in bootstrap
    assert "trap bootstrap_cleanup EXIT" in bootstrap


def test_macos_uu_reporter_is_change_driven_and_process_scoped() -> None:
    reporter = (ROOT / "contrib" / "macos" / "uuremote-route-reporter.sh").read_text(
        encoding="ascii"
    )
    installer = (
        ROOT / "contrib" / "macos" / "install-uuremote-route-reporter.sh"
    ).read_text(encoding="ascii")
    template = (
        ROOT / "contrib" / "macos" / "com.lachlan.astrill-lazy-uuremote-route.plist.in"
    ).read_text(encoding="ascii")

    assert "/Applications/UURemote\\.app/" in reporter
    assert "lsof -nP -a" in reporter
    assert "-iUDP" in reporter
    assert "app-flow" in reporter
    assert "PasswordAuthentication=no" in reporter
    assert 'nc -z -G 1 "$router_address" 22' in reporter
    assert "eval " not in reporter
    assert 'SUPPORT_DIR="$HOME/Library/Application Support/Astrill Lazy Router"' in (
        installer
    )
    assert 'PROGRAM="$SUPPORT_DIR/uuremote-route-reporter"' in installer
    assert "<integer>30</integer>" in template
    assert "ASTRILL_LAZY_ROUTER_ADDRESS" in template
    assert "ASTRILL_LAZY_HEARTBEAT_SECONDS" in template


def test_failed_astrill_switch_restores_settings_and_original_tunnel_state() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")
    match = re.search(
        r"^switch_astrill\(\) \{\n(?P<body>.*?)"
        r"^\}\n\nrestart_astrill_for_managed_rebase\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    switch = match["body"]

    state_snapshot = "initially_connected=false"
    state_up = "initially_connected=true"
    requested_start = '/dev/astrill/astrillvpn start > "$BASE/astrill-switch.log"'
    restore_settings = 'done < "$rollback_file"'
    rollback_stop = '/dev/astrill/astrillvpn stop >> "$BASE/astrill-switch.log"'
    state_branch = 'if [ "$initially_connected" = true ]; then'
    rollback_start = '/dev/astrill/astrillvpn start >> "$BASE/astrill-switch.log"'
    original_failure = (
        'fail "new Astrill server did not connect; previous settings were restored"'
    )
    unverified_failure = (
        'fail "new Astrill server did not connect; '
        'previous connection state could not be verified"'
    )

    assert switch.index(state_snapshot) < switch.index(state_up)
    assert switch.index(state_up) < switch.index(requested_start)
    assert switch.index(requested_start) < switch.index(restore_settings)
    assert switch.index(restore_settings) < switch.index(rollback_stop)
    assert switch.index(rollback_stop) < switch.index(state_branch)
    assert switch.index(state_branch) < switch.index(rollback_start)
    assert switch.index(rollback_start) < switch.index(original_failure)
    assert switch.index(original_failure) < switch.index(unverified_failure)

    disconnected_rollback = switch[
        switch.index(rollback_stop) : switch.index(state_branch)
    ]
    assert "astrillvpn start" not in disconnected_rollback
    assert "rollback_stopped=false" in disconnected_rollback
    assert 'while [ "$attempts" -lt 65 ]' in disconnected_rollback
    assert "astrill/openvpn.conf" in disconnected_rollback

    connected_rollback = switch[
        switch.index(state_branch) : switch.index("ensure_routes >/dev/null 2>&1")
    ]
    assert rollback_start in connected_rollback
    assert "rollback_connected=false" in connected_rollback
    assert "rollback_connected=true" in connected_rollback
    assert "rollback_verified=false" in connected_rollback
    assert 'while [ "$attempts" -lt "$ASTRILL_CONNECT_ATTEMPTS" ]' in connected_rollback
    assert "rollback_verified=true" in connected_rollback
    assert '[ "$rollback_stopped" = true ]' in connected_rollback
    assert '[ "$rollback_connected" = true ]' in connected_rollback

    assert "rollback_verified=$rollback_stopped" in switch
    assert switch.count('rm -f "$rollback_file"') == 4
    assert "could not verify the original connection state" in switch
    assert switch.index("ensure_routes >/dev/null 2>&1") < switch.index(
        original_failure
    )


def test_router_policy_precedence_follows_astrill_lifecycle() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")

    prepare = re.search(
        r"^prepare_for_astrill_start\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert prepare is not None
    assert prepare["body"].index("ensure_vpn_fail_closed") < prepare["body"].index(
        "remove_owned_ip_rules"
    )
    assert "clear_rebase_required" in prepare["body"]

    switch = re.search(
        r"^switch_astrill\(\) \{\n(?P<body>.*?)"
        r"^\}\n\nrestart_astrill_for_managed_rebase\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert switch is not None
    assert switch["body"].index("prepare_for_astrill_start") < switch["body"].index(
        "/dev/astrill/astrillvpn start"
    )
    assert switch["body"].count("prepare_for_astrill_start") == 2
    assert switch["body"].index("prepare_for_astrill_stop") < switch["body"].index(
        "/dev/astrill/astrillvpn stop"
    )

    connection = re.search(
        r"^set_astrill_connection\(\) \{\n(?P<body>.*?)^\}\n\nusage\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert connection is not None
    already_connected = connection["body"].index("ensure_runtime")
    prepare_start = connection["body"].index("prepare_for_astrill_start")
    native_start = connection["body"].index("/dev/astrill/astrillvpn start")
    assert already_connected < prepare_start < native_start
    assert "ensure_runtime >/dev/null 2>&1 || true" not in connection["body"]

    routes = re.search(
        r"^ensure_routes\(\) \{\n(?P<body>.*?)^\}\n\napply_runtime\(\)",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert routes is not None
    assert "if ! tunnel_is_up; then" in routes["body"]
    assert routes["body"].index("ensure_vpn_fail_closed") < routes["body"].index(
        "remove_owned_ip_rules"
    )
    assert "wait_for_native_rules_stable" in routes["body"]
    assert routes["body"].count("install_owned_overlay") == 1
    assert "disable_vpn_fail_closed" in routes["body"]
    assert "rebase_is_required" in routes["body"]
    assert "mark_recorded_overlay_for_rebase" in routes["body"]

    down_branch = connection["body"][connection["body"].index("down)") :]
    assert down_branch.index("prepare_for_astrill_stop") < down_branch.index(
        "/dev/astrill/astrillvpn stop"
    )
    assert "restart_astrill_for_managed_rebase" in connection["body"]


def test_router_removes_only_exact_companion_rules_and_idles_when_ready() -> None:
    controller = (ROOT / "router" / "alctl").read_text(encoding="ascii")

    delete_lines = [
        line.strip() for line in controller.splitlines() if "ip rule del" in line
    ]
    assert delete_lines == [
        'ip rule del pref "$pref" fwmark "$mark/$MARK_MASK" lookup "$table" ||'
    ]
    assert "lookup (110|111|112|113|114)" in controller
    assert 'ip rule del pref "$pref" 2>/dev/null' not in controller

    fail_closed = re.search(
        r"^ensure_vpn_fail_closed\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert fail_closed is not None
    body = fail_closed["body"].lstrip()
    assert body.startswith("vpn_fail_closed_is_ready && return 0")
    assert '-I FORWARD 1 -j "$VPN_FAIL_CHAIN"' in body

    overlay = re.search(
        r"^owned_overlay_is_valid\(\) \{\n(?P<body>.*?)^\}\n",
        controller,
        re.MULTILINE | re.DOTALL,
    )
    assert overlay is not None
    assert "load_owned_pref_state" in overlay["body"]
    assert '[ "$owned_vpn_pref" -lt "$current_native_pref" ]' in overlay["body"]

    stop = controller[controller.index("    stop)") :]
    stop = stop[: stop.index("    watchdog-loop)")]
    assert "cleanup_policy ||" in stop
    assert 'fail "could not completely remove the companion policy runtime"' in stop


def test_ddwrt_banner_is_removed_from_ssh_errors() -> None:
    output = (
        "DD-WRT v3.0-r62374 mega\n"
        "Release: 10/19/25\n"
        "Board: Linksys E4200\n"
        "actual failure\n"
    )
    assert _clean_ssh_stderr(output) == "actual failure"


@pytest.mark.parametrize(
    "message",
    (
        "root@192.168.1.1: Permission denied (publickey,password).",
        "Permission denied, please try again.",
        "No supported authentication methods available",
        "Authentication failed",
    ),
)
def test_ssh_authentication_failure_is_detected(message: str) -> None:
    assert is_ssh_authentication_failure(message)


@pytest.mark.parametrize(
    "message",
    (
        "router command timed out after 30 seconds",
        "Connection timed out",
        "Connection refused",
        "Host key verification failed.",
        "policy runtime is not ready",
    ),
)
def test_non_authentication_router_errors_do_not_request_password(
    message: str,
) -> None:
    assert not is_ssh_authentication_failure(message)


@pytest.mark.skipif(SHELL is None, reason="POSIX shell is unavailable")
def test_router_clients_merge_lan_sources_and_exclude_wan(tmp_path: Path) -> None:
    assert SHELL is not None
    leases = tmp_path / "dnsmasq.leases"
    leases.write_text(
        "2000000000 AA:BB:CC:DD:EE:01 192.168.1.10 * client-id\n",
        encoding="ascii",
    )
    arp = tmp_path / "arp"
    arp.write_text(
        "IP address HW type Flags HW address Mask Device\n"
        "192.168.1.10 0x1 0x2 aa:bb:cc:dd:ee:01 * br0\n"
        "192.168.1.30 0x1 0x2 aa:bb:cc:dd:ee:03 * br0\n"
        "192.168.2.1 0x1 0x2 aa:bb:cc:dd:ee:ff * vlan2\n",
        encoding="ascii",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nvram = bin_dir / "nvram"
    nvram.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  lan_ifname) printf 'br0' ;;\n"
        "  static_leases) "
        "printf 'AA:BB:CC:DD:EE:01=laptop=192.168.1.10=1440' ;;\n"
        "  dhcp_staticlist) "
        "printf '<AA:BB:CC:DD:EE:02=printer=192.168.1.20=1440>' ;;\n"
        "esac\n",
        encoding="ascii",
    )
    nvram.chmod(0o755)
    path = f"{bin_dir.as_posix()}:{os.environ['PATH']}"
    if os.name == "nt":
        path = f"{bin_dir.as_posix()}:/usr/bin:/bin"
    environment = {
        **os.environ,
        "PATH": path,
        "ASTRILL_LAZY_BASE": (tmp_path / "runtime").as_posix(),
        "ASTRILL_LAZY_LEASES_FILE": leases.as_posix(),
        "ASTRILL_LAZY_ARP_FILE": arp.as_posix(),
    }
    result = subprocess.run(
        [SHELL, (ROOT / "router" / "alctl").as_posix(), "clients", "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    clients = json.loads(result.stdout)
    by_mac = {client["mac"]: client for client in clients}

    assert set(by_mac) == {
        "aa:bb:cc:dd:ee:01",
        "aa:bb:cc:dd:ee:02",
        "aa:bb:cc:dd:ee:03",
    }
    assert by_mac["aa:bb:cc:dd:ee:01"] == {
        "address": "192.168.1.10",
        "mac": "aa:bb:cc:dd:ee:01",
        "hostname": "laptop",
        "expires": 2000000000,
        "source": "dhcp,static,arp",
        "active": True,
    }
    assert by_mac["aa:bb:cc:dd:ee:02"]["source"] == "static"
    assert by_mac["aa:bb:cc:dd:ee:02"]["active"] is False
    assert by_mac["aa:bb:cc:dd:ee:03"]["source"] == "arp"
    assert by_mac["aa:bb:cc:dd:ee:03"]["active"] is True
