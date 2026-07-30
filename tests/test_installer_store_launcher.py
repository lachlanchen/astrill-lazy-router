from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from astrill_lazy.autostart import (
    autostart_path,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from astrill_lazy.installer import (
    INSTALL_PRECONDITION_ERROR,
    LEGACY_STARTUP_LINE,
    STARTUP_LINE,
    UNCOMPRESSED_DIGEST_STARTUP_LINE,
    UNINSTALL_PRECONDITION_ERROR,
    RouterInstaller,
    _canonical_stored_bootstrap,
    _compressed_rule_document,
    _InstallSnapshot,
    _normalized_bootstrap,
    _persisted_core_boot_is_valid,
    _rule_storage_migration_commands,
    _stored_bootstrap,
    build_router_package,
    find_router_root,
)
from astrill_lazy.launcher import parse_command
from astrill_lazy.router import RouterError
from astrill_lazy.store import ConfigStore, default_uu_rule

ROUTER_VERSION = (find_router_root() / "VERSION").read_text(encoding="ascii").strip()


class _ExactNvramFake:
    values: dict[str, str]

    def nvram_get_exact(self, key: str) -> str:
        return self.values.get(key, "")

    def nvram_is_set(self, key: str) -> bool:
        return key in self.values


class _CurrentPresenceMixin:
    expected_md5 = ""
    expected_bootstrap_md5 = ""

    def companion_presence(self) -> dict[str, object]:
        return {
            "installed": True,
            "version": ROUTER_VERSION,
            "package_md5": self.expected_md5,
            "bootstrap_md5": self.expected_bootstrap_md5,
            "package_integrity": True,
            "bootstrap_integrity": True,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": ("/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"),
        }


def _legacy_package_values(
    *,
    version: str = "0.2.old",
    bootstrap: str = "#!/bin/sh\nexit 0\n",
) -> dict[str, str]:
    output = BytesIO()
    files = {
        "alctl": b"#!/bin/sh\n# legacy controller\n",
        "alapi": b"#!/bin/sh\n# legacy api\n",
        "alpage": b"#!/bin/sh\n# legacy page\n",
        "VERSION": version.encode("ascii") + b"\n",
    }
    with (
        gzip.GzipFile(fileobj=output, mode="wb", compresslevel=9, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w") as package,
    ):
        for name, payload in files.items():
            info = tarfile.TarInfo(f"astrill-lazy/{name}")
            info.size = len(payload)
            info.mode = 0o700 if name != "VERSION" else 0o600
            package.addfile(info, BytesIO(payload))
    archive = output.getvalue()
    encoded = base64.b64encode(archive).decode("ascii")
    chunks = [
        encoded[offset : offset + 1800] for offset in range(0, len(encoded), 1800)
    ]
    values = {
        "astrill_lazy_installed": "1",
        "astrill_lazy_version": version,
        "astrill_lazy_pkg_count": str(len(chunks)),
        "astrill_lazy_pkg_md5": hashlib.md5(
            archive,
            usedforsecurity=False,
        ).hexdigest(),
        "astrill_lazy_bootstrap": bootstrap,
    }
    values.update(
        {f"astrill_lazy_pkg_{index}": chunk for index, chunk in enumerate(chunks)}
    )
    return values


def test_router_package_is_deterministic_and_contains_only_runtime_files() -> None:
    root = find_router_root()
    first = build_router_package(root)
    second = build_router_package(root)
    assert first == second
    assert len(first) < 20_000

    with tarfile.open(fileobj=BytesIO(first), mode="r:gz") as archive:
        names = archive.getnames()
        assert names == [
            "astrill-lazy/alctl",
            "astrill-lazy/alapi",
            "astrill-lazy/alpage",
            "astrill-lazy/VERSION",
        ]
        assert all("key" not in name and "backup" not in name for name in names)


def test_router_package_canonicalizes_crlf_version(tmp_path: Path) -> None:
    root = tmp_path / "router"
    root.mkdir()
    for name in ("alctl", "alapi", "alpage"):
        (root / name).write_bytes(b"#!/bin/sh\n")
    (root / "VERSION").write_bytes(b"0.2.3\r\n")

    package = build_router_package(root)

    with tarfile.open(fileobj=BytesIO(package), mode="r:gz") as archive:
        version_file = archive.extractfile("astrill-lazy/VERSION")
        assert version_file is not None
        assert version_file.read() == b"0.2.3\n"


def test_installer_exposes_all_deployment_fingerprints() -> None:
    class Client:
        pass

    installer = RouterInstaller(Client())  # type: ignore[arg-type]
    bootstrap = _normalized_bootstrap(installer.router_root)
    stored_bootstrap = _stored_bootstrap(installer.router_root)
    bootstrap_archive = base64.b64decode(stored_bootstrap, validate=True)
    helper = (installer.router_root / "alhybrid").read_bytes()

    assert (
        installer.expected_bootstrap_md5
        == hashlib.md5(
            _canonical_stored_bootstrap(installer.router_root),
            usedforsecurity=False,
        ).hexdigest()
    )
    assert gzip.decompress(bootstrap_archive) == bootstrap
    assert bootstrap_archive[4:8] == b"\0\0\0\0"
    assert bootstrap_archive[9] == 255
    assert len(stored_bootstrap) < len(bootstrap) // 2
    assert (
        installer.expected_hybrid_helper_md5
        == hashlib.md5(
            helper,
            usedforsecurity=False,
        ).hexdigest()
    )
    assert "astrill_lazy_bootstrap_md5" in STARTUP_LINE
    assert "md5sum" in STARTUP_LINE
    assert "uudecode -o -" in STARTUP_LINE
    assert "gzip -dc" in STARTUP_LINE
    assert 'ASTRILL_LAZY_BOOTSTRAP_MD5="$d" /bin/sh' in STARTUP_LINE
    assert "printf '%s\\n' \"$b\"|md5sum" in STARTUP_LINE


def test_hybrid_helper_deployment_is_bound_to_package_identity() -> None:
    class Client:
        arguments: tuple[bytes, str, str, str] | None = None

        def ensure_hybrid_helper(
            self,
            payload: bytes,
            digest: str,
            *,
            expected_version: str,
            expected_package_md5: str,
        ) -> str:
            self.arguments = (
                payload,
                digest,
                expected_version,
                expected_package_md5,
            )
            return "installed"

    client = Client()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    result = installer.ensure_hybrid_helper()

    assert result.action == "installed"
    assert client.arguments is not None
    payload, helper_md5, version, package_md5 = client.arguments
    assert helper_md5 == installer.expected_hybrid_helper_md5
    assert hashlib.md5(payload, usedforsecurity=False).hexdigest() == helper_md5
    assert version == installer.expected_version
    assert package_md5 == installer.expected_package_md5


def test_rule_storage_migration_compresses_legacy_documents_before_package() -> None:
    document = "# astrill-lazy-rules-v1\n" + (
        "service:uu-remote\t1\tdirect\tcidr\t8.221.56.176/32\n" * 80
    )
    commands = _rule_storage_migration_commands(
        {
            "astrill_lazy_rules": document,
            "astrill_lazy_rules_gz": "",
            "astrill_lazy_rules_previous": "",
            "astrill_lazy_rules_previous_gz": "",
        }
    )

    assert commands[0].startswith("nvram set astrill_lazy_rules_gz=")
    assert commands[1] == "nvram unset astrill_lazy_rules"
    assert len(_compressed_rule_document(document)) < len(document.encode("ascii"))


def test_rule_storage_migration_keeps_valid_compressed_document() -> None:
    document = "# astrill-lazy-rules-v1\n"
    compressed = _compressed_rule_document(document)

    commands = _rule_storage_migration_commands(
        {
            "astrill_lazy_rules": document,
            "astrill_lazy_rules_gz": compressed,
            "astrill_lazy_rules_previous": "",
            "astrill_lazy_rules_previous_gz": "",
        }
    )

    assert commands == ["nvram unset astrill_lazy_rules"]


def test_config_store_round_trip_is_private(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.rules = [default_uu_rule()]
    store.enabled_extensions = ["core-catalog"]
    store.router_host = "192.168.50.1"
    store.router_user = "root"
    store.router_port = 2222
    store.router_identity = "~/.ssh/test-router"
    store.router_use_ssh_config = False
    store.companion_enabled = False
    store.read_only = False
    store.save()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600

    loaded = ConfigStore(path)
    assert loaded.rules[0].selector == "uu-remote"
    assert loaded.enabled_extensions == ["core-catalog"]
    assert loaded.router_host == "192.168.50.1"
    assert loaded.router_user == "root"
    assert loaded.router_port == 2222
    assert loaded.router_identity == "~/.ssh/test-router"
    assert loaded.router_use_ssh_config is False
    assert loaded.companion_enabled is False
    assert loaded.read_only is False
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["companion_enabled"] is False
    assert document["read_only"] is False
    assert "password" not in document


def test_fresh_config_store_starts_native_only_and_read_only(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "missing.json")

    assert store.router_host == "192.168.1.1"
    assert store.router_user == "root"
    assert store.router_port == 22
    assert store.router_identity == "~/.ssh/astrill_lazy_router_ed25519"
    assert store.router_use_ssh_config is False
    assert store.companion_enabled is False
    assert store.read_only is True
    assert store.rules == []


def test_legacy_config_keeps_its_writable_companion_behavior(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "router_host": "legacy-router",
                "active_region": "active-astrill",
                "enabled_extensions": ["core-catalog"],
                "rules": [],
            }
        ),
        encoding="utf-8",
    )

    store = ConfigStore(path)

    assert store.companion_enabled is True
    assert store.read_only is False
    assert store.router_use_ssh_config is True

    store.save()
    assert ConfigStore(path).router_use_ssh_config is True


@pytest.mark.skipif(
    os.name == "nt",
    reason="application network namespaces are an Ubuntu-only provider",
)
def test_parse_application_command() -> None:
    executable, arguments = parse_command("/usr/bin/printf '%s' hello")
    assert executable == "/usr/bin/printf"
    assert arguments == ["%s", "hello"]


def test_parse_application_command_rejects_missing_file() -> None:
    with pytest.raises(ValueError, match="not found"):
        parse_command("/does/not/exist")


@pytest.mark.skipif(
    os.name == "nt",
    reason="freedesktop autostart is an Ubuntu-only provider",
)
def test_desktop_autostart_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    executable = tmp_path / "Astrill Lazy GUI"
    executable.write_text("#!/bin/sh\n", encoding="ascii")

    installed = enable_autostart(executable)
    assert installed == autostart_path()
    assert installed.stat().st_mode & 0o777 == 0o644
    assert is_autostart_enabled()
    document = installed.read_text(encoding="utf-8")
    assert f'Exec="{executable}"' in document
    assert "X-GNOME-Autostart-enabled=true" in document

    disable_autostart()
    assert not is_autostart_enabled()


def test_router_reconcile_skips_current_runtime() -> None:
    class CurrentClient(_CurrentPresenceMixin):
        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "package_md5": self.expected_md5,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": ROUTER_VERSION,
                "astrill_lazy_pkg_md5": self.expected_md5,
                "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
            }
            return values.get(arguments[-1], "")

    client = CurrentClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    result = installer.ensure()
    assert result.action == "none"


def test_companion_check_requires_confirmation_when_not_installed() -> None:
    class MissingClient:
        def companion_presence(self) -> dict[str, object]:
            return {"installed": False, "version": None, "runtime": False}

    check = RouterInstaller(MissingClient()).check()  # type: ignore[arg-type]

    assert check.action == "install"
    assert check.installed_version is None
    assert "not installed" in check.reason


def test_companion_check_reuses_preloaded_healthy_snapshot() -> None:
    class SnapshotOnlyClient:
        def companion_presence(self) -> dict[str, object]:
            raise AssertionError("presence must come from the monitor snapshot")

        def status(self) -> dict[str, object]:
            raise AssertionError("status must come from the monitor snapshot")

    installer = RouterInstaller(SnapshotOnlyClient())  # type: ignore[arg-type]
    check = installer.check(
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
            "package_md5": installer.expected_package_md5,
            "bootstrap_md5": installer.expected_bootstrap_md5,
            "package_integrity": True,
            "bootstrap_integrity": True,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": ("/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"),
        },
        status={
            "version": ROUTER_VERSION,
            "package_md5": installer.expected_package_md5,
            "jump_installed": True,
            "watchdog": True,
            "policy_health": "ready",
            "precedence_ok": True,
        },
    )

    assert check.action == "none"
    assert check.installed_version == ROUTER_VERSION


def test_companion_check_repairs_a_present_but_degraded_runtime() -> None:
    class SnapshotOnlyClient:
        def companion_presence(self) -> dict[str, object]:
            raise AssertionError("presence must come from the monitor snapshot")

        def status(self) -> dict[str, object]:
            raise AssertionError("status must come from the monitor snapshot")

    degraded = {
        "version": ROUTER_VERSION,
        "jump_installed": True,
        "watchdog": True,
        "policy_health": "degraded",
        "precedence_ok": False,
        "vpn_state": "up",
    }
    installer = RouterInstaller(SnapshotOnlyClient())  # type: ignore[arg-type]
    degraded["package_md5"] = installer.expected_package_md5
    check = installer.check(
        presence={
            "installed": True,
            "version": ROUTER_VERSION,
            "runtime": True,
            "package_md5": installer.expected_package_md5,
            "bootstrap_md5": installer.expected_bootstrap_md5,
            "package_integrity": True,
            "bootstrap_integrity": True,
            "rc_startup": STARTUP_LINE,
            "mypage_scripts": ("/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"),
        },
        status=degraded,
    )

    assert check.action == "repair"
    assert check.status == degraded
    assert "policy routing" in check.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("package_integrity", False),
        ("bootstrap_integrity", False),
        ("rc_startup", f"# {STARTUP_LINE}"),
        ("mypage_scripts", "/tmp/astrill-lazy/alpage"),
    ],
)
def test_companion_check_requires_exact_integrity_and_active_hooks(
    field: str,
    value: object,
) -> None:
    class SnapshotOnlyClient:
        pass

    installer = RouterInstaller(SnapshotOnlyClient())  # type: ignore[arg-type]
    presence: dict[str, object] = {
        "installed": True,
        "version": ROUTER_VERSION,
        "package_md5": installer.expected_package_md5,
        "bootstrap_md5": installer.expected_bootstrap_md5,
        "package_integrity": True,
        "bootstrap_integrity": True,
        "rc_startup": STARTUP_LINE,
        "mypage_scripts": "/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi",
    }
    presence[field] = value

    check = installer.check(presence=presence, status=None)

    assert check.action == "install"
    assert "fingerprint" in check.reason


def test_same_version_with_old_package_fingerprint_requires_upgrade() -> None:
    class OldSameVersionClient:
        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "package_md5": "0" * 32,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

        def companion_presence(self) -> dict[str, object]:
            return {
                "installed": True,
                "version": ROUTER_VERSION,
                "package_md5": "0" * 32,
                "bootstrap_md5": "0" * 32,
                "package_integrity": False,
                "bootstrap_integrity": False,
                "rc_startup": "",
                "mypage_scripts": "",
            }

    installer = RouterInstaller(OldSameVersionClient())  # type: ignore[arg-type]

    with pytest.raises(
        RouterError,
        match="requires Install / Upgrade confirmation",
    ):
        installer.ensure(allow_install=False)


def test_automatic_reconcile_rejects_a_healthy_transient_runtime() -> None:
    class TransientRuntimeClient:
        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

        def companion_presence(self) -> dict[str, object]:
            return {"installed": False, "version": None}

    installer = RouterInstaller(TransientRuntimeClient())  # type: ignore[arg-type]

    with pytest.raises(
        RouterError,
        match="requires Install / Upgrade confirmation",
    ):
        installer.ensure(allow_install=False)


def test_automatic_reconcile_cannot_install_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingClient:
        def status(self) -> dict[str, object]:
            raise RouterError("runtime missing")

        def ping(self) -> bool:
            return True

        def companion_presence(self) -> dict[str, object]:
            return {"installed": False, "version": None}

    monkeypatch.setattr("astrill_lazy.installer.time.sleep", lambda _seconds: None)
    installer = RouterInstaller(MissingClient())  # type: ignore[arg-type]
    monkeypatch.setattr(
        installer,
        "install",
        lambda: pytest.fail("automatic reconcile must not install"),
    )

    with pytest.raises(RouterError, match="requires Install / Upgrade confirmation"):
        installer.ensure(allow_install=False)


def test_router_reconcile_repairs_runtime_before_reinstalling() -> None:
    class RepairableClient(_CurrentPresenceMixin):
        def __init__(self) -> None:
            self.repaired = False
            self.expected_md5 = ""
            self.expected_bootstrap_md5 = ""

        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "package_md5": self.expected_md5,
                "jump_installed": self.repaired,
                "watchdog": self.repaired,
                "policy_health": "ready" if self.repaired else "degraded",
                "precedence_ok": self.repaired,
            }

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            if arguments[:2] == ["nvram", "get"]:
                assert timeout is None
                values = {
                    "astrill_lazy_installed": "1",
                    "astrill_lazy_version": ROUTER_VERSION,
                    "astrill_lazy_pkg_md5": self.expected_md5,
                    "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
                }
                return values.get(arguments[-1], "")
            assert timeout == 75
            self.repaired = True
            return ""

    client = RepairableClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    result = installer.ensure()
    assert result.action == "repaired"
    assert client.repaired


def test_router_reconcile_repairs_a_connected_but_degraded_policy_runtime() -> None:
    class DegradedClient(_CurrentPresenceMixin):
        def __init__(self) -> None:
            self.repaired = False
            self.expected_md5 = ""
            self.expected_bootstrap_md5 = ""

        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "package_md5": self.expected_md5,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready" if self.repaired else "degraded",
                "precedence_ok": self.repaired,
                "vpn_state": "up",
            }

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            if arguments[:2] == ["nvram", "get"]:
                assert timeout is None
                values = {
                    "astrill_lazy_installed": "1",
                    "astrill_lazy_version": ROUTER_VERSION,
                    "astrill_lazy_pkg_md5": self.expected_md5,
                    "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
                }
                return values.get(arguments[-1], "")
            assert arguments == ["/tmp/astrill-lazy/alctl", "start"]
            assert timeout == 75
            self.repaired = True
            return ""

    client = DegradedClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    result = installer.ensure()

    assert result.action == "repaired"
    assert result.status["policy_health"] == "ready"
    assert client.repaired


def test_router_reconcile_returns_live_degraded_status_after_best_effort() -> None:
    class StillDegradedClient(_CurrentPresenceMixin):
        def __init__(self) -> None:
            self.start_calls = 0
            self.expected_md5 = ""
            self.expected_bootstrap_md5 = ""

        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "package_md5": self.expected_md5,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "degraded",
                "precedence_ok": False,
                "vpn_state": "up",
                "last_reconcile_error": "native rules did not stabilize",
            }

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            if arguments[:2] == ["nvram", "get"]:
                assert timeout is None
                values = {
                    "astrill_lazy_installed": "1",
                    "astrill_lazy_version": ROUTER_VERSION,
                    "astrill_lazy_pkg_md5": self.expected_md5,
                    "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
                }
                return values.get(arguments[-1], "")
            assert arguments == ["/tmp/astrill-lazy/alctl", "start"]
            assert timeout == 75
            self.start_calls += 1
            return ""

    client = StillDegradedClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    result = installer.ensure(allow_install=False)

    assert result.action == "degraded"
    assert result.status["vpn_state"] == "up"
    assert result.status["policy_health"] == "degraded"
    assert result.status["last_reconcile_error"] == ("native rules did not stabilize")
    assert client.start_calls == 1


def test_router_reconcile_reconstructs_current_stored_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StoredClient(_CurrentPresenceMixin):
        def __init__(self) -> None:
            self.reconstructed = False
            self.expected_md5 = ""
            self.expected_bootstrap_md5 = ""

        def status(self) -> dict[str, object]:
            if not self.reconstructed:
                raise RouterError("runtime is not ready")
            return {
                "version": ROUTER_VERSION,
                "package_md5": self.expected_md5,
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

        def ping(self) -> bool:
            return True

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": ROUTER_VERSION,
                "astrill_lazy_pkg_md5": self.expected_md5,
                "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
            }
            return values[arguments[-1]]

        def run_script(self, script: str, *, timeout: int) -> str:
            assert "missing or empty bootstrap" in script
            assert "bootstrap digest mismatch" in script
            assert 'ASTRILL_LAZY_BOOTSTRAP_MD5="$bootstrap_digest"' in script
            assert "uudecode -o -" in script
            assert "gzip -dc" in script
            assert '/bin/sh -c "$bootstrap_script"' in script
            assert timeout == 75
            self.reconstructed = True
            return ""

    monkeypatch.setattr("astrill_lazy.installer.time.sleep", lambda _seconds: None)
    client = StoredClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    result = installer.ensure()
    assert result.action == "repaired"
    assert client.reconstructed


def test_router_reconcile_does_not_rewrite_identical_broken_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenClient(_CurrentPresenceMixin):
        def __init__(self) -> None:
            self.expected_md5 = ""
            self.expected_bootstrap_md5 = ""

        def status(self) -> dict[str, object]:
            return {
                "version": ROUTER_VERSION,
                "jump_installed": False,
                "watchdog": False,
                "policy_health": "degraded",
                "precedence_ok": False,
            }

        def raw(self, arguments: list[str], *, timeout: int | None = None) -> str:
            if arguments == ["/tmp/astrill-lazy/alctl", "start"]:
                assert timeout == 75
                raise RouterError("start failed")
            assert timeout is None
            values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_version": ROUTER_VERSION,
                "astrill_lazy_pkg_md5": self.expected_md5,
                "astrill_lazy_bootstrap_md5": self.expected_bootstrap_md5,
            }
            return values[arguments[-1]]

    client = BrokenClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    client.expected_md5 = installer.expected_package_md5
    client.expected_bootstrap_md5 = installer.expected_bootstrap_md5
    monkeypatch.setattr(
        installer,
        "install",
        lambda: pytest.fail("identical package must not be rewritten"),
    )
    with pytest.raises(RouterError, match="explicit rewrite"):
        installer.ensure()


def test_persisted_core_preflight_matches_router_boot_rejection_cases() -> None:
    valid = (
        "# astrill-lazy-rules-v1\n"
        "safe\t1\t100\tcidr\t192.0.2.1/32\tdirect\tany\t-\tSafe\tsafe\n"
    )
    assert _persisted_core_boot_is_valid({"astrill_lazy_rules": valid})
    assert not _persisted_core_boot_is_valid(
        {
            "astrill_lazy_rules": "invalid-current",
            "astrill_lazy_rules_previous": valid,
        }
    )
    assert not _persisted_core_boot_is_valid(
        {"astrill_lazy_rules": valid.replace("\n", "\r\n")}
    )
    assert not _persisted_core_boot_is_valid(
        {"astrill_lazy_rules": valid.replace("safe\t1", "\nsafe\t1")}
    )
    assert not _persisted_core_boot_is_valid(
        {
            "astrill_lazy_rules": valid.replace(
                "192.0.2.1/32",
                "2001:db8::1/128",
            )
        }
    )


def test_install_refuses_an_unsafe_rollback_snapshot_before_mutation() -> None:
    valid_previous = (
        "# astrill-lazy-rules-v1\n"
        "safe\t1\t100\tcidr\t192.0.2.1/32\tdirect\tany\t-\tSafe\tsafe\n"
    )

    class UnsafeSnapshotClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                "astrill_lazy_rules": "invalid-current",
                "astrill_lazy_rules_previous": valid_previous,
                "rc_startup": "native-startup",
                "mypage_scripts": "native-page",
            }
            self.mutation_scripts: list[str] = []

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            self.mutation_scripts.append(script)
            return ""

    client = UnsafeSnapshotClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="not reboot-valid"):
        installer.install()

    assert client.mutation_scripts == []


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_chunk",
        "invalid_base64",
        "package_digest",
        "bootstrap_digest",
        "package_count",
        "startup_launcher",
        "compressed_launcher_raw_payload",
        "raw_launcher_compressed_payload",
        "legacy_launcher_compressed_payload",
    ],
)
def test_install_refuses_corrupt_installed_rollback_targets_before_mutation(
    corruption: str,
) -> None:
    values = {
        **_legacy_package_values(),
        "astrill_lazy_rules": "# astrill-lazy-rules-v1\n",
        "rc_startup": f"native-startup\n{LEGACY_STARTUP_LINE}",
        "mypage_scripts": "native-page",
    }
    if corruption == "missing_chunk":
        values.pop("astrill_lazy_pkg_0")
    elif corruption == "invalid_base64":
        values["astrill_lazy_pkg_0"] = "*"
    elif corruption == "package_digest":
        values["astrill_lazy_pkg_md5"] = "0" * 32
    elif corruption == "bootstrap_digest":
        values["astrill_lazy_bootstrap_md5"] = "0" * 32
    elif corruption == "package_count":
        values["astrill_lazy_pkg_count"] = "0"
    elif corruption == "startup_launcher":
        values["rc_startup"] = f"# {LEGACY_STARTUP_LINE}"
    elif corruption == "compressed_launcher_raw_payload":
        bootstrap = values["astrill_lazy_bootstrap"]
        values["astrill_lazy_bootstrap_md5"] = hashlib.md5(
            (bootstrap.rstrip("\n") + "\n").encode("ascii"),
            usedforsecurity=False,
        ).hexdigest()
        values["rc_startup"] = STARTUP_LINE
    elif corruption == "raw_launcher_compressed_payload":
        bootstrap = _stored_bootstrap(find_router_root())
        values["astrill_lazy_bootstrap"] = bootstrap
        values["astrill_lazy_bootstrap_md5"] = hashlib.md5(
            (bootstrap + "\n").encode("ascii"),
            usedforsecurity=False,
        ).hexdigest()
        values["rc_startup"] = UNCOMPRESSED_DIGEST_STARTUP_LINE
    elif corruption == "legacy_launcher_compressed_payload":
        values["astrill_lazy_bootstrap"] = _stored_bootstrap(find_router_root())

    class CorruptRollbackClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = dict(values)
            self.mutation_scripts: list[str] = []

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            self.mutation_scripts.append(script)
            return ""

    client = CorruptRollbackClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="captured installed rollback"):
        installer.install()

    assert client.mutation_scripts == []


def test_snapshot_rejects_huge_corrupt_chunk_count_before_range_or_io() -> None:
    class HugeCountClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {"astrill_lazy_pkg_count": "9" * 1000}

        def run_script(self, script: str, *, timeout: int) -> str:
            raise AssertionError("headroom must not be read after invalid count")

    installer = RouterInstaller(HugeCountClient())  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="chunk count.*safe NVRAM range"):
        installer._capture_install_snapshot()


def test_generated_transaction_cas_preserves_newlines_and_empty_presence() -> None:
    bootstrap = "#!/bin/sh\nprintf ready\n"
    snapshot = _InstallSnapshot(
        values={
            "astrill_lazy_bootstrap": bootstrap,
            "empty_owned": "",
            "absent_owned": "",
        },
        present=frozenset({"astrill_lazy_bootstrap", "empty_owned"}),
        package_count=0,
        nvram_free_bytes=100000,
    )
    installer = RouterInstaller.__new__(RouterInstaller)
    script = installer._install_transaction_script(
        snapshot,
        dict(snapshot.values),
        snapshot.present,
        [],
        stored_rules={},
        old_package_count=0,
        new_package_count=0,
        projected_growth=0,
    )

    assert (
        f"assert_nvram astrill_lazy_bootstrap {bootstrap.encode().hex()} 1"
    ) in script
    assert "assert_nvram empty_owned '' 1" in script
    assert "assert_nvram absent_owned '' 0" in script
    assert "nvram set empty_owned=" in script
    assert "nvram unset absent_owned" in script
    assert "hexdump -v -e" in script
    assert "od -An" not in script
    compare = script.index("assert_nvram absent_owned")
    pending = script.index(
        "[ ! -e /tmp/astrill-lazy/policy-transaction ]",
        compare,
    )
    mutate = script.index("if ! install_nvram", pending)
    assert compare < pending < mutate


def test_preflight_counts_set_empty_and_unset_as_distinct_nvram_states() -> None:
    snapshot = _InstallSnapshot(
        values={"owned_empty": ""},
        present=frozenset({"owned_empty"}),
        package_count=0,
        nvram_free_bytes=100000,
    )
    installer = RouterInstaller.__new__(RouterInstaller)

    growth = installer._preflight_install(
        snapshot,
        {"owned_empty": ""},
        frozenset(),
    )

    assert growth == -(len("owned_empty") + 2)


def test_recovery_refuses_snapshot_when_commit_fails() -> None:
    values = {
        "astrill_lazy_installed": "",
        "astrill_lazy_version": "",
        "astrill_lazy_pkg_count": "",
        "astrill_lazy_pkg_md5": "",
        "astrill_lazy_bootstrap": "",
    }
    snapshot = _InstallSnapshot(
        values=values,
        present=frozenset(),
        package_count=0,
        nvram_free_bytes=100000,
    )

    class CommitFailureClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = dict(values)
            self.script = ""

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 300
            self.script = script
            raise RouterError("nvram commit failed")

    client = CommitFailureClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    recovered, detail = installer._recover_failed_install(
        snapshot,
        expected_install=dict(values),
        expected_install_present=frozenset(),
    )

    assert not recovered
    assert "commit failed" in detail
    branch = client.script.index("if snapshot_matches && snapshot_chunks_match; then")
    commit = client.script.index("nvram commit >/dev/null", branch)
    verify = client.script.index("snapshot_matches", commit)
    assert branch < commit < verify


def test_recovery_commit_and_install_transactions_trap_hup() -> None:
    values = {
        "astrill_lazy_installed": "",
        "astrill_lazy_version": "",
        "astrill_lazy_pkg_count": "",
        "astrill_lazy_pkg_md5": "",
        "astrill_lazy_bootstrap": "",
    }
    snapshot = _InstallSnapshot(
        values=values,
        present=frozenset(),
        package_count=0,
        nvram_free_bytes=100000,
    )

    class CaptureClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = dict(values)
            self.script = ""

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 300
            self.script = script
            raise RouterError("simulated HUP")

    client = CaptureClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    installer._recover_failed_install(
        snapshot,
        expected_install=dict(values),
        expected_install_present=frozenset(),
    )

    branch = client.script.index("if snapshot_matches && snapshot_chunks_match; then")
    trap = client.script.index("trap recovery_cleanup EXIT INT TERM HUP", branch)
    commit = client.script.index("nvram commit >/dev/null", trap)
    assert branch < trap < commit
    assert "trap release_lock EXIT INT TERM HUP" in client.script


def test_recovery_to_uninstalled_state_requires_stop_and_explicit_audits() -> None:
    values = {
        "astrill_lazy_installed": "",
        "astrill_lazy_version": "",
        "astrill_lazy_pkg_count": "",
        "astrill_lazy_pkg_md5": "",
        "astrill_lazy_bootstrap": "",
    }
    snapshot = _InstallSnapshot(
        values=values,
        present=frozenset(),
        package_count=0,
        nvram_free_bytes=100000,
    )

    class StopFailureClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = dict(values)
            self.script = ""

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 300
            self.script = script
            raise RouterError("could not stop the attempted runtime")

    client = StopFailureClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    recovered, detail = installer._recover_failed_install(
        snapshot,
        expected_install=dict(values),
        expected_install_present=frozenset(),
    )

    assert not recovered
    assert "could not stop" in detail
    stop = client.script.index("/tmp/astrill-lazy/alctl stop")
    acquire = client.script.index("acquire_lock ||", stop)
    assert stop < acquire
    assert "rm -rf /tmp/astrill-lazy" not in client.script
    assert 'rm -rf "$runtime_path"' in client.script
    assert "runtime_is_quiescent" in client.script
    assert "iptables -w 10 -t mangle -S" in client.script
    assert "iptables -w 10 -t filter -S" in client.script
    assert "ip rule show" in client.script
    assert "ip route show table 212" in client.script
    assert "ip route show table 213" in client.script
    release = client.script.rindex("release_lock")
    nonrecursive = client.script.index('rmdir "$BASE"', release)
    assert release < nonrecursive


def test_uninstalled_recovery_refuses_runtime_that_returns_after_stop() -> None:
    snapshot_values = {
        "astrill_lazy_installed": "",
        "astrill_lazy_version": "",
        "astrill_lazy_pkg_count": "",
        "astrill_lazy_pkg_md5": "",
        "astrill_lazy_bootstrap": "",
        "astrill_lazy_pkg_0": "",
    }
    snapshot = _InstallSnapshot(
        values=snapshot_values,
        present=frozenset(),
        package_count=0,
        nvram_free_bytes=100000,
    )
    installed_values = {
        **snapshot_values,
        "astrill_lazy_installed": "1",
        "astrill_lazy_version": ROUTER_VERSION,
        "astrill_lazy_pkg_count": "1",
        "astrill_lazy_pkg_md5": "1" * 32,
        "astrill_lazy_bootstrap": "#!/bin/sh\nexit 0\n",
        "astrill_lazy_pkg_0": "package",
    }
    installed_present = frozenset(
        key for key, value in installed_values.items() if value
    )

    class RuntimeRaceClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = dict(installed_values)
            self.script = ""

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 300
            self.script = script
            raise RouterError(
                "astrill-lazy installer recovery refused: policy runtime "
                "residue returned after stop"
            )

    client = RuntimeRaceClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]
    recovered, detail = installer._recover_failed_install(
        snapshot,
        expected_install=installed_values,
        expected_install_present=installed_present,
    )

    assert not recovered
    assert "residue returned" in detail
    assert client.values == installed_values
    stop = client.script.index("/tmp/astrill-lazy/alctl stop")
    acquire = client.script.index("acquire_lock ||", stop)
    state_cas = client.script.index(
        "elif installed_matches && installed_chunks_match",
        acquire,
    )
    quiescence = client.script.index("if ! runtime_is_quiescent", state_cas)
    mutation = client.script.index("nvram commit >/dev/null", quiescence)
    assert stop < acquire < state_cas < quiescence < mutation


def test_install_keyboard_interrupt_runs_guarded_recovery() -> None:
    class CancelClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                "rc_startup": "native-startup",
                "mypage_scripts": "native-page",
            }
            self.scripts: list[str] = []

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            assert timeout == 300
            self.scripts.append(script)
            if len(self.scripts) == 1:
                raise KeyboardInterrupt
            return ""

    client = CancelClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt) as cancelled:
        installer.install()

    assert len(client.scripts) == 2
    assert any("Recovery verified" in note for note in cancelled.value.__notes__)
    assert "if snapshot_matches && snapshot_chunks_match; then" in (client.scripts[1])


def test_reconstruct_refuses_missing_bootstrap() -> None:
    class MissingBootstrapClient:
        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 75
            assert "missing or empty bootstrap" in script
            raise RouterError("missing or empty bootstrap")

        def status(self) -> dict[str, object]:
            raise AssertionError("missing bootstrap must not be accepted")

    installer = RouterInstaller(MissingBootstrapClient())  # type: ignore[arg-type]
    with pytest.raises(RouterError, match="could not be reconstructed"):
        installer._reconstruct_current_package()


def test_runtime_health_requires_matching_package_digest() -> None:
    class Client:
        pass

    installer = RouterInstaller(Client())  # type: ignore[arg-type]
    base = {
        "version": installer.expected_version,
        "jump_installed": True,
        "watchdog": True,
        "policy_health": "ready",
        "precedence_ok": True,
    }
    assert not installer._runtime_is_current(base)
    assert not installer._runtime_is_current({**base, "package_md5": "0" * 32})
    assert installer._runtime_is_current(
        {**base, "package_md5": installer.expected_package_md5}
    )
    assert not installer._runtime_is_healthy_version(
        {**base, "package_md5": "0" * 32},
        installer.expected_version,
        "1" * 32,
    )


def test_install_preflight_rejects_low_nvram_without_mutation() -> None:
    class LowHeadroomClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.scripts: list[str] = []
            self.values = {
                "rc_startup": "native-startup",
                "mypage_scripts": "native-page",
            }

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            assert timeout == 30
            self.scripts.append(script)
            return "100\n"

        def status(self) -> dict[str, object]:
            raise AssertionError("preflight rejection must happen before status")

    client = LowHeadroomClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="insufficient NVRAM headroom"):
        installer.install()

    assert len(client.scripts) == 1
    assert "nvram show" in client.scripts[0]
    assert "nvram set" not in client.scripts[0]
    assert "nvram unset" not in client.scripts[0]
    assert "nvram commit" not in client.scripts[0]


def test_install_refuses_concurrent_rule_drift_without_mutation() -> None:
    original_rules = (
        "# astrill-lazy-rules-v1\n"
        "old\t1\t100\tcidr\t192.0.2.1/32\tdirect\tany\t-\tOld\told"
    )

    class ConcurrentRuleClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                "astrill_lazy_rules": original_rules,
                "rc_startup": "native-startup",
                "mypage_scripts": "native-page",
            }
            self.transaction = ""
            self.installer_mutations = 0

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            assert timeout == 300
            self.transaction = script
            # A core writer completed while the installer was waiting for the
            # shared controller lock. The installer's first locked CAS sees it.
            self.values["astrill_lazy_rules"] = (
                "# astrill-lazy-rules-v1\n"
                "newer\t1\t100\tcidr\t198.51.100.2/32\tdirect\tany\t-"
                "\tNewer\tnewer"
            )
            raise RouterError(
                f"{INSTALL_PRECONDITION_ERROR} NVRAM changed at astrill_lazy_rules"
            )

        def status(self) -> dict[str, object]:
            raise AssertionError("a refused transaction must not inspect runtime")

    client = ConcurrentRuleClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="before any NVRAM mutation"):
        installer.install()

    assert client.installer_mutations == 0
    assert "newer" in client.values["astrill_lazy_rules"]
    lock = client.transaction.index("acquire_lock ||")
    first_pid_read = client.transaction.index(
        'lock_pid=$(cat "$LOCK_DIR/pid"',
        client.transaction.index("acquire_lock() {"),
    )
    grace = client.transaction.index("sleep 1", first_pid_read)
    second_pid_read = client.transaction.index(
        'lock_pid=$(cat "$LOCK_DIR/pid"',
        grace,
    )
    reclaim = client.transaction.index('rm -f "$LOCK_DIR/pid"', second_pid_read)
    compare = client.transaction.index("assert_nvram astrill_lazy_rules", lock)
    recheck = client.transaction.index("free_bytes=$(nvram_free_bytes)", compare)
    mutate = client.transaction.index("if ! install_nvram", recheck)
    assert first_pid_read < grace < second_pid_read < reclaim < lock
    assert lock < compare < recheck < mutate


def test_install_refuses_live_free_space_shrink_without_mutation() -> None:
    class ShrinkingHeadroomClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                "rc_startup": "native-startup",
                "mypage_scripts": "native-page",
            }
            self.transaction = ""
            self.installer_mutations = 0

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            assert timeout == 300
            self.transaction = script
            raise RouterError(
                f"{INSTALL_PRECONDITION_ERROR} live NVRAM headroom shrank"
            )

        def status(self) -> dict[str, object]:
            raise AssertionError("a refused transaction must not inspect runtime")

    client = ShrinkingHeadroomClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="before any NVRAM mutation"):
        installer.install()

    assert client.installer_mutations == 0
    lock = client.transaction.index("acquire_lock ||")
    recheck = client.transaction.index("free_bytes=$(nvram_free_bytes)", lock)
    mutate = client.transaction.index("if ! install_nvram", recheck)
    assert lock < recheck < mutate
    assert "projected_free=$((free_bytes -" in client.transaction


def test_partial_install_mutation_restores_in_the_same_locked_session() -> None:
    class PartialMutationClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.original = {
                **_legacy_package_values(),
                "astrill_lazy_rules": "# astrill-lazy-rules-v1\n",
                "astrill_lazy_rules_gz": "",
                "astrill_lazy_rules_previous": "",
                "astrill_lazy_rules_previous_gz": "",
                "astrill_lazy_previous_rc_startup": "factory-startup",
                "astrill_lazy_previous_mypage_scripts": "factory-page",
                "rc_startup": f"native-startup\n{LEGACY_STARTUP_LINE}",
                "mypage_scripts": "native-page",
            }
            self.values = dict(self.original)
            self.scripts: list[str] = []

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            assert timeout == 300
            self.scripts.append(script)
            if len(self.scripts) == 1:
                self.values["astrill_lazy_version"] = ROUTER_VERSION
                self.values["astrill_lazy_pkg_0"] = "partial-new-package"
                # Model restore_snapshot running before this SSH session exits.
                self.values = dict(self.original)
                raise RouterError(
                    "astrill-lazy installer mutation failed; original NVRAM "
                    "restored in the same session"
                )
            assert "if snapshot_matches; then" in script
            assert script.index("acquire_lock ||") < script.index(
                "if snapshot_matches; then"
            )
            return ""

        def status(self) -> dict[str, object]:
            return {
                "version": "0.2.old",
                "package_md5": self.original["astrill_lazy_pkg_md5"],
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

    client = PartialMutationClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="Recovery verified"):
        installer.install()

    assert client.values == client.original
    install_script = client.scripts[0]
    acquire = install_script.index("acquire_lock ||")
    mutate = install_script.index("if ! install_nvram", acquire)
    restore = install_script.index("if restore_snapshot && snapshot_matches", mutate)
    release = install_script.index("release_lock", restore)
    bootstrap = install_script.rindex('/bin/sh -c "$bootstrap_script"')
    assert acquire < mutate < restore < release < bootstrap


def test_failed_bootstrap_recovery_refuses_newer_policy_state() -> None:
    class NewerPolicyClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                **_legacy_package_values(),
                "astrill_lazy_rules": "# astrill-lazy-rules-v1\n",
                "astrill_lazy_rules_gz": "",
                "astrill_lazy_rules_previous": "",
                "astrill_lazy_rules_previous_gz": "",
                "astrill_lazy_previous_rc_startup": "factory-startup",
                "astrill_lazy_previous_mypage_scripts": "factory-page",
                "rc_startup": f"native-startup\n{LEGACY_STARTUP_LINE}",
                "mypage_scripts": "native-page",
            }
            self.scripts: list[str] = []
            self.validated = False
            self.newer_rules = (
                "# astrill-lazy-rules-v1\n"
                "newer\t1\t100\tcidr\t203.0.113.7/32\tdirect\tany\t-"
                "\tNewer\tnewer"
            )

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            if arguments[:2] == [
                "/tmp/astrill-lazy/alctl",
                "validate-persisted-core",
            ]:
                assert timeout == 120
                self.validated = True
                return "{}"
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            assert timeout == 300
            self.scripts.append(script)
            if len(self.scripts) == 1:
                return ""
            assert "elif installed_matches; then" in script
            assert script.index("acquire_lock ||") < script.index(
                "if snapshot_matches; then"
            )
            raise RouterError(
                "astrill-lazy installer recovery refused: a newer policy, "
                "package, or startup state is present"
            )

        def status(self) -> dict[str, object]:
            assert self.validated
            self.values["astrill_lazy_rules"] = self.newer_rules
            return {
                "version": ROUTER_VERSION,
                "jump_installed": False,
                "watchdog": False,
                "policy_health": "degraded",
                "precedence_ok": False,
            }

    client = NewerPolicyClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(
        RouterError,
        match="Recovery was not verified.*newer policy",
    ):
        installer.install()

    assert client.values["astrill_lazy_rules"] == client.newer_rules
    assert len(client.scripts) == 2


def test_failed_upgrade_restores_snapshot_and_verifies_old_runtime() -> None:
    rule_document = "# astrill-lazy-rules-v1\n" + (
        "old-rule\t1\t100\tcidr\t192.0.2.1/32\tdirect\tany\t-\tOld\told-rule\n" * 32
    )

    class FailedUpgradeClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.original = {
                **_legacy_package_values(),
                "astrill_lazy_rules": rule_document,
                "astrill_lazy_rules_gz": "",
                "astrill_lazy_rules_previous": "old-previous-rules",
                "astrill_lazy_rules_previous_gz": "",
                "astrill_lazy_previous_rc_startup": "factory-startup",
                "astrill_lazy_previous_mypage_scripts": "factory-page",
                "rc_startup": f"native-startup\n{LEGACY_STARTUP_LINE}",
                "mypage_scripts": "native-page",
            }
            self.values = dict(self.original)
            self.mutation_scripts: list[str] = []
            self.status_calls = 0
            self.runtime_verify_script = ""

        def raw(
            self,
            arguments: list[str],
            *,
            timeout: int | None = None,
        ) -> str:
            if arguments[:2] == [
                "/tmp/astrill-lazy/alctl",
                "validate-persisted-core",
            ]:
                assert timeout == 120
                return "{}"
            assert timeout is None
            return self.values.get(arguments[-1], "")

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 30:
                return "100000\n"
            if timeout == 45:
                self.runtime_verify_script = script
                return ""
            assert timeout == 300
            self.mutation_scripts.append(script)
            if len(self.mutation_scripts) == 1:
                self.values["astrill_lazy_version"] = ROUTER_VERSION
                self.values["astrill_lazy_pkg_md5"] = "2" * 32
                self.values["astrill_lazy_pkg_2"] = "new-package"
            else:
                self.values = dict(self.original)
            return ""

        def status(self) -> dict[str, object]:
            self.status_calls += 1
            if self.status_calls == 1:
                return {
                    "version": ROUTER_VERSION,
                    "package_md5": "2" * 32,
                    "jump_installed": False,
                    "watchdog": False,
                    "policy_health": "degraded",
                    "precedence_ok": False,
                }
            return {
                "version": "0.2.old",
                "jump_installed": True,
                "watchdog": True,
                "policy_health": "ready",
                "precedence_ok": True,
            }

    client = FailedUpgradeClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="Recovery verified") as failure:
        installer.install()

    assert "previous companion 0.2.old was restored" in str(failure.value)
    assert client.values == client.original
    assert client.status_calls == 2
    assert len(client.mutation_scripts) == 2
    install_script, recovery_script = client.mutation_scripts
    assert install_script.count("nvram commit") == 2
    assert recovery_script.count("nvram commit") == 3
    assert "nvram set astrill_lazy_pkg_0=" in recovery_script
    assert "astrill_lazy_rules=" in recovery_script
    assert "astrill_lazy_rules_previous=" in recovery_script
    assert "astrill_lazy_previous_rc_startup=" in recovery_script
    assert "rc_startup=native-startup" in recovery_script
    assert "mypage_scripts=native-page" in recovery_script
    assert "restored bootstrap changed before launch" in recovery_script
    assert "ASTRILL_LAZY_RECOVERY=1" in recovery_script
    assert "ASTRILL_LAZY_RECOVERY_VERSION=0.2.old" in recovery_script
    assert "ASTRILL_LAZY_RECOVERY_PACKAGE_MD5=" in recovery_script
    assert "ASTRILL_LAZY_RECOVERY_BOOTSTRAP_MD5=" in recovery_script
    assert "/bin/sh -c " in recovery_script
    assert "legacy controller" not in recovery_script
    assert "md5sum /tmp/astrill-lazy/alctl" in client.runtime_verify_script
    assert "md5sum /tmp/astrill-lazy/VERSION" in client.runtime_verify_script


def test_router_uninstall_audits_cleanup_and_preserves_native_state() -> None:
    native_status = {
        "vpn_state": "up",
        "astrill_server_id": 1109,
        "astrill_protocol": 2,
    }

    class InstalledClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.script = ""
            self.calls: list[tuple[int, str]] = []
            self.values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_pkg_count": "12",
                **{
                    f"astrill_lazy_pkg_{index}": f"package-{index}"
                    for index in range(12)
                },
                "rc_startup": "original-command\n" + STARTUP_LINE,
                "mypage_scripts": (
                    "native-page /tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"
                ),
            }

        def native_astrill_status(self) -> dict[str, object]:
            return dict(native_status)

        def run_script(self, script: str, *, timeout: int) -> str:
            self.calls.append((timeout, script))
            if timeout == 75:
                assert "/tmp/astrill-lazy/alctl stop" in script
                return ""
            if timeout == 30:
                chunks = [
                    key.removeprefix("astrill_lazy_pkg_")
                    for key in self.values
                    if key.startswith("astrill_lazy_pkg_")
                    and key.removeprefix("astrill_lazy_pkg_").isdigit()
                ]
                return "\n".join(sorted(chunks, key=int)) + ("\n" if chunks else "")
            assert timeout == 300
            self.script = script
            self.values = {
                "rc_startup": "original-command",
                "mypage_scripts": "native-page",
            }
            return ""

    client = InstalledClient()
    result = RouterInstaller(client).uninstall()  # type: ignore[arg-type]
    assert result == native_status
    assert "astrill_lazy_pkg_0" in client.script
    assert "astrill_lazy_pkg_1" in client.script
    assert "8\n9\n10\n11" in client.script
    assert "astrill_lazy_rules_gz" in client.script
    assert "astrill_lazy_rules_previous_gz" in client.script
    assert "nvram unset astrill_lazy_bootstrap_md5" in client.script
    assert "nvram unset astrill_lazy_rules" in client.script
    assert "nvram unset astrill_lazy_rules_previous" in client.script
    assert 'rm -rf "$runtime_path"' in client.script
    assert "iptables -w 10 -t mangle -S" in client.script
    assert "iptables -w 10 -t filter -S" in client.script
    assert "lookup (212|213)" in client.script
    assert "native-page" in client.script
    assert "/tmp/astrill-lazy/alpage" not in next(
        line
        for line in client.script.splitlines()
        if line.strip().startswith("nvram set mypage_scripts=")
    )
    assert [timeout for timeout, _script in client.calls] == [75, 30, 300, 30]


def test_uninstall_refuses_concurrent_policy_drift_before_nvram_mutation() -> None:
    native_status = {
        "vpn_state": "down",
        "astrill_server_id": 0,
        "astrill_protocol": 0,
    }

    class ConcurrentClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.values = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_pkg_count": "0",
                "astrill_lazy_rules": "# astrill-lazy-rules-v1\n",
                "rc_startup": STARTUP_LINE,
                "mypage_scripts": ("/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"),
            }
            self.transaction = ""

        def native_astrill_status(self) -> dict[str, object]:
            return dict(native_status)

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 75:
                return ""
            if timeout == 30:
                return ""
            assert timeout == 300
            self.transaction = script
            self.values["astrill_lazy_rules"] = (
                "# astrill-lazy-rules-v1\n"
                "newer\t1\t100\tcidr\t198.51.100.3/32\tdirect\tany\t-"
                "\tNewer\tnewer\n"
            )
            raise RouterError(
                f"{UNINSTALL_PRECONDITION_ERROR} NVRAM changed after runtime stop"
            )

    client = ConcurrentClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="before any NVRAM mutation"):
        installer.uninstall()

    assert "newer" in client.values["astrill_lazy_rules"]
    acquire = client.transaction.index("acquire_lock ||")
    compare = client.transaction.index("if ! snapshot_matches", acquire)
    audit = client.transaction.index("if ! runtime_is_quiescent", compare)
    mutate = client.transaction.index("if ! uninstall_nvram", audit)
    assert acquire < compare < audit < mutate


def test_partial_uninstall_failure_restores_exact_snapshot_under_lock() -> None:
    native_status = {
        "vpn_state": "up",
        "astrill_server_id": 1109,
        "astrill_protocol": 2,
    }

    class PartialClient(_ExactNvramFake):
        def __init__(self) -> None:
            self.original = {
                "astrill_lazy_installed": "1",
                "astrill_lazy_pkg_count": "1",
                "astrill_lazy_pkg_0": "package",
                "astrill_lazy_rules": "# astrill-lazy-rules-v1\n",
                "rc_startup": STARTUP_LINE,
                "mypage_scripts": ("/tmp/astrill-lazy/alpage /tmp/astrill-lazy/alapi"),
            }
            self.values = dict(self.original)
            self.transaction = ""

        def native_astrill_status(self) -> dict[str, object]:
            return dict(native_status)

        def run_script(self, script: str, *, timeout: int) -> str:
            if timeout == 75:
                return ""
            if timeout == 30:
                return "0\n"
            assert timeout == 300
            self.transaction = script
            self.values["astrill_lazy_installed"] = ""
            self.values.pop("astrill_lazy_pkg_0")
            # Model restore_snapshot completing before the SSH session exits.
            self.values = dict(self.original)
            raise RouterError(
                "astrill-lazy uninstaller failed; the exact NVRAM snapshot "
                "was restored in the same session"
            )

    client = PartialClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="attempted exact NVRAM restoration"):
        installer.uninstall()

    assert client.values == client.original
    mutate = client.transaction.index("if ! uninstall_nvram")
    restore = client.transaction.index(
        "if restore_snapshot && snapshot_matches",
        mutate,
    )
    release = client.transaction.index("release_lock", restore)
    assert mutate < restore < release
    assert "trap uninstall_cleanup EXIT INT TERM HUP" in client.transaction
    assert "ip route show table 212" in client.transaction
    assert "ip route show table 213" in client.transaction


def test_uninstall_stop_failure_never_snapshots_or_mutates_nvram() -> None:
    class StopFailureClient:
        def __init__(self) -> None:
            self.calls = 0

        def native_astrill_status(self) -> dict[str, object]:
            return {}

        def run_script(self, script: str, *, timeout: int) -> str:
            self.calls += 1
            assert timeout == 75
            assert "/tmp/astrill-lazy/alctl stop" in script
            raise RouterError("stop failed")

        def nvram_get_exact(self, key: str) -> str:
            raise AssertionError(f"must not snapshot {key}")

        def nvram_is_set(self, key: str) -> bool:
            raise AssertionError(f"must not inspect {key}")

    client = StopFailureClient()
    installer = RouterInstaller(client)  # type: ignore[arg-type]

    with pytest.raises(RouterError, match="stop failed"):
        installer.uninstall()

    assert client.calls == 1
