from __future__ import annotations

import base64
import gzip
import hashlib
import re
import shlex
import sys
import tarfile
import time
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from .router import RouterClient, RouterError

PACKAGE_FILES = ("alctl", "alapi", "alpage", "VERSION")
HYBRID_HELPER_FILE = "alhybrid"
POLICY_PAGE_FILE = "alpage-ui"
CHUNK_SIZE = 1800
LEGACY_STARTUP_LINE = "nvram get astrill_lazy_bootstrap | sh;"
PREVIOUS_STARTUP_LINE = (
    'astrill_lazy_bootstrap_script="$(nvram get astrill_lazy_bootstrap)" '
    '&& [ -n "$astrill_lazy_bootstrap_script" ] '
    "&& printf '%s\\n' \"$astrill_lazy_bootstrap_script\" | /bin/sh;"
)
MD5_SHELL_PATTERN = "[0-9a-f]" * 32
UNCOMPRESSED_DIGEST_STARTUP_LINE = (
    'astrill_lazy_bootstrap_script="$(nvram get astrill_lazy_bootstrap)"; '
    'astrill_lazy_bootstrap_digest="$(nvram get astrill_lazy_bootstrap_md5)"; '
    'case "$astrill_lazy_bootstrap_digest" in '
    f"{MD5_SHELL_PATTERN}) "
    "astrill_lazy_bootstrap_actual=\"$(printf '%s\\n' "
    '"$astrill_lazy_bootstrap_script" | md5sum | awk \'{print $1}\')"; '
    '[ -n "$(printf \'%s\' "$astrill_lazy_bootstrap_script" | '
    "tr -d '[:space:]')\" ] && "
    '[ "$astrill_lazy_bootstrap_actual" = "$astrill_lazy_bootstrap_digest" ] && '
    'ASTRILL_LAZY_BOOTSTRAP_MD5="$astrill_lazy_bootstrap_digest" '
    '/bin/sh -c "$astrill_lazy_bootstrap_script" ;; esac;'
)
STARTUP_LINE = (
    'b="$(nvram get astrill_lazy_bootstrap)";'
    'd="$(nvram get astrill_lazy_bootstrap_md5)";'
    '[ -n "$b" ]&&'
    '[ "$(printf \'%s\\n\' "$b"|md5sum|cut -d\' \' -f1)" = "$d" ]&&'
    "{ printf 'begin-base64 600 bootstrap.gz\\n%s\\n====\\n' \"$b\"|"
    "uudecode -o - 2>/dev/null|gzip -dc 2>/dev/null|"
    'ASTRILL_LAZY_BOOTSTRAP_MD5="$d" /bin/sh;}'
)
PAGE_COMMANDS = (
    "/tmp/astrill-lazy/alpage",
    "/tmp/astrill-lazy/alapi",
)
RULE_STORAGE_PAIRS = (
    ("astrill_lazy_rules", "astrill_lazy_rules_gz"),
    ("astrill_lazy_rules_previous", "astrill_lazy_rules_previous_gz"),
)
PACKAGE_METADATA_KEYS = (
    "astrill_lazy_installed",
    "astrill_lazy_version",
    "astrill_lazy_pkg_count",
    "astrill_lazy_pkg_md5",
    "astrill_lazy_bootstrap",
    "astrill_lazy_bootstrap_md5",
)
STARTUP_METADATA_KEYS = (
    "astrill_lazy_previous_rc_startup",
    "astrill_lazy_previous_mypage_scripts",
    "rc_startup",
    "mypage_scripts",
)
MIN_NVRAM_FREE_BYTES = 2048
MAX_RULE_BYTES = 6144
MAX_PACKAGE_CHUNKS = 64
MAX_PACKAGE_ARCHIVE_BYTES = 128 * 1024
MAX_PACKAGE_EXPANDED_BYTES = 2 * 1024 * 1024
MAX_BOOTSTRAP_ARCHIVE_BYTES = 32 * 1024
MAX_BOOTSTRAP_EXPANDED_BYTES = 128 * 1024
CONTROLLER_LOCK_DIR = "/tmp/astrill-lazy/controller.lock"
INSTALL_PRECONDITION_ERROR = "astrill-lazy installer precondition failed:"
UNINSTALL_PRECONDITION_ERROR = "astrill-lazy uninstaller precondition failed:"


@dataclass(frozen=True)
class InstallResult:
    version: str
    package_bytes: int
    package_sha256: str
    nvram_chunks: int
    policy_page: int
    api_page: int
    status: dict[str, Any]


@dataclass(frozen=True)
class InstallPreflight:
    version: str
    installed_version: str | None
    package_bytes: int
    package_sha256: str
    nvram_chunks: int
    nvram_free_before: int
    projected_growth: int
    projected_free: int
    minimum_free: int
    can_install: bool


@dataclass(frozen=True)
class EnsureResult:
    status: dict[str, Any]
    action: str


@dataclass(frozen=True)
class CompanionCheck:
    action: str
    expected_version: str
    installed_version: str | None
    status: dict[str, Any] | None
    reason: str


@dataclass(frozen=True)
class HybridHelperResult:
    action: str
    helper_bytes: int
    helper_md5: str


@dataclass(frozen=True)
class _InstallSnapshot:
    values: dict[str, str]
    present: frozenset[str]
    package_count: int
    nvram_free_bytes: int


@dataclass(frozen=True)
class _RollbackTarget:
    bootstrap: str
    bootstrap_md5: str
    runtime_md5: tuple[tuple[str, str], ...]
    status_has_package_md5: bool


@dataclass(frozen=True)
class _PreparedInstall:
    archive: bytes
    chunks: tuple[str, ...]
    version: str
    package_md5: str
    package_sha256: str
    snapshot: _InstallSnapshot
    rollback_target: _RollbackTarget | None
    pages: tuple[str, ...]
    assignments: tuple[tuple[str, str], ...]
    expected_install: dict[str, str]
    expected_install_present: frozenset[str]
    stored_rules: dict[str, str]
    projected_growth: int
    projected_free: int


class RouterInstaller:
    def __init__(self, client: RouterClient) -> None:
        self.client = client
        self.router_root = find_router_root()

    @property
    def expected_version(self) -> str:
        return (self.router_root / "VERSION").read_text(encoding="ascii").strip()

    @property
    def expected_package_md5(self) -> str:
        archive = build_router_package(self.router_root)
        return hashlib.md5(archive, usedforsecurity=False).hexdigest()

    @property
    def expected_bootstrap_md5(self) -> str:
        payload = _canonical_stored_bootstrap(self.router_root)
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()

    @property
    def expected_hybrid_helper_md5(self) -> str:
        payload = (self.router_root / HYBRID_HELPER_FILE).read_bytes()
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()

    def ensure_hybrid_helper(self) -> HybridHelperResult:
        """Stage the optional overlay engine in RAM without touching NVRAM."""

        helper = self.router_root / HYBRID_HELPER_FILE
        if not helper.is_file():
            raise FileNotFoundError(f"router hybrid helper was not found: {helper}")
        payload = helper.read_bytes()
        digest = self.expected_hybrid_helper_md5
        action = self.client.ensure_hybrid_helper(
            payload,
            digest,
            expected_version=self.expected_version,
            expected_package_md5=self.expected_package_md5,
        )
        page = self.router_root / POLICY_PAGE_FILE
        stage_asset = getattr(self.client, "ensure_runtime_asset", None)
        if page.is_file() and callable(stage_asset):
            page_payload = page.read_bytes()
            stage_asset(
                page_payload,
                hashlib.md5(
                    page_payload,
                    usedforsecurity=False,
                ).hexdigest(),
                target="/tmp/astrill-lazy/alpage-ui",
                label="policy page",
                expected_version=self.expected_version,
                expected_package_md5=self.expected_package_md5,
            )
        return HybridHelperResult(
            action=action,
            helper_bytes=len(payload),
            helper_md5=digest,
        )

    def check(
        self,
        *,
        presence: dict[str, Any] | None = None,
        status: dict[str, Any] | None = None,
    ) -> CompanionCheck:
        if presence is None:
            presence = self.client.companion_presence()
        installed_version = presence.get("version")
        if not presence.get("installed"):
            return CompanionCheck(
                "install",
                self.expected_version,
                None,
                None,
                "The router companion is not installed.",
            )
        if installed_version != self.expected_version:
            return CompanionCheck(
                "install",
                self.expected_version,
                str(installed_version) if installed_version else None,
                None,
                "The installed companion does not match the desktop package.",
            )
        if not self._presence_package_is_current(presence):
            return CompanionCheck(
                "install",
                self.expected_version,
                str(installed_version),
                status,
                "The stored companion package or bootstrap fingerprint does "
                "not match the desktop package.",
            )
        if status is None:
            try:
                status = self.client.status()
            except RouterError:
                status = None
        if status is not None and self._runtime_is_current(status):
            return CompanionCheck(
                "none",
                self.expected_version,
                str(installed_version),
                status,
                "The router companion is current and healthy.",
            )
        if status is not None and self._runtime_is_present(status):
            return CompanionCheck(
                "repair",
                self.expected_version,
                str(installed_version),
                status,
                "The companion is running, but policy routing needs repair.",
            )
        if self._stored_package_is_current():
            return CompanionCheck(
                "repair",
                self.expected_version,
                str(installed_version),
                status,
                "The current companion is stored but its runtime needs repair.",
            )
        return CompanionCheck(
            "install",
            self.expected_version,
            str(installed_version),
            status,
            "The companion runtime cannot be repaired from the stored package.",
        )

    def ensure(self, *, allow_install: bool = True) -> EnsureResult:
        try:
            status = self.client.status()
        except RouterError:
            if not self.client.ping():
                raise
            time.sleep(5)
            try:
                status = self.client.status()
            except RouterError:
                if self._stored_package_is_current():
                    return self._reconstruct_current_package()
                return self._install_or_require_confirmation(allow_install)

        stored_identity = self._stored_package_identity()
        if self._runtime_is_current(status) and stored_identity is True:
            return EnsureResult(status, "none")
        if status.get("version") == self.expected_version and stored_identity is True:
            latest = status
            try:
                self.client.raw(
                    ["/tmp/astrill-lazy/alctl", "start"],
                    timeout=75,
                )
                latest = self.client.status()
            except RouterError:
                try:
                    latest = self.client.status()
                except RouterError:
                    latest = status
            if self._runtime_is_current(latest):
                return EnsureResult(latest, "repaired")
            if self._runtime_is_present(latest):
                return EnsureResult(latest, "degraded")
            if stored_identity is True:
                raise RouterError(
                    "the current router package is installed but its runtime "
                    "could not be repaired; use Install / Upgrade for an "
                    "explicit rewrite"
                )
        elif stored_identity is True:
            return self._reconstruct_current_package()
        return self._install_or_require_confirmation(allow_install)

    def _install_or_require_confirmation(self, allow_install: bool) -> EnsureResult:
        if not allow_install:
            raise RouterError(
                "companion installation or rewrite requires Install / Upgrade "
                "confirmation"
            )
        result = self.install()
        return EnsureResult(result.status, "installed")

    def _reconstruct_current_package(self) -> EnsureResult:
        try:
            self.client.run_script(
                "\n".join(_bootstrap_shell("stored")) + "\n",
                timeout=75,
            )
            status = self.client.status()
        except RouterError as exc:
            raise RouterError(
                "the current router package is stored but could not be "
                "reconstructed; use Install / Upgrade for an explicit rewrite"
            ) from exc
        if self._runtime_is_current(status):
            return EnsureResult(status, "repaired")
        if self._runtime_is_present(status):
            return EnsureResult(status, "degraded")
        raise RouterError(
            "the current router package was reconstructed but its runtime "
            "is not healthy"
        )

    def _stored_package_is_current(self) -> bool:
        return self._stored_package_identity() is True

    def _stored_package_identity(self) -> bool | None:
        presence = self.client.companion_presence()
        installed = presence.get("installed")
        version = presence.get("version")
        package_md5 = presence.get("package_md5")
        bootstrap_md5 = presence.get("bootstrap_md5")
        if not installed and not version and not package_md5 and not bootstrap_md5:
            return None
        return (
            installed is True
            and version == self.expected_version
            and self._presence_package_is_current(presence)
        )

    def _presence_package_is_current(
        self,
        presence: dict[str, Any],
    ) -> bool:
        package_md5 = presence.get("package_md5")
        bootstrap_md5 = presence.get("bootstrap_md5")
        startup = presence.get("rc_startup")
        pages = presence.get("mypage_scripts")
        if startup is None:
            startup = self._nvram_get("rc_startup")
        if pages is None:
            pages = self._nvram_get("mypage_scripts")
        return (
            str(package_md5) == self.expected_package_md5
            and str(bootstrap_md5) == self.expected_bootstrap_md5
            and presence.get("package_integrity") is True
            and presence.get("bootstrap_integrity") is True
            and _persistent_hooks_are_current(str(startup), str(pages))
        )

    def _runtime_is_current(self, status: dict[str, Any]) -> bool:
        return (
            self._runtime_is_present(status)
            and status.get("package_md5") == self.expected_package_md5
            and status.get("policy_health") == "ready"
            and status.get("precedence_ok") is True
        )

    def _runtime_is_present(self, status: dict[str, Any]) -> bool:
        return (
            status.get("version") == self.expected_version
            and status.get("jump_installed") is True
            and status.get("watchdog") is True
        )

    def preflight_install(self) -> InstallPreflight:
        """Project the exact install transaction without changing the router."""

        prepared = self._prepare_install()
        installed_version = (
            prepared.snapshot.values["astrill_lazy_version"].strip() or None
        )
        return InstallPreflight(
            version=prepared.version,
            installed_version=installed_version,
            package_bytes=len(prepared.archive),
            package_sha256=prepared.package_sha256,
            nvram_chunks=len(prepared.chunks),
            nvram_free_before=prepared.snapshot.nvram_free_bytes,
            projected_growth=prepared.projected_growth,
            projected_free=prepared.projected_free,
            minimum_free=MIN_NVRAM_FREE_BYTES,
            can_install=prepared.projected_free >= MIN_NVRAM_FREE_BYTES,
        )

    def _prepare_install(self) -> _PreparedInstall:
        archive = build_router_package(self.router_root)
        encoded = base64.b64encode(archive).decode("ascii")
        chunks = tuple(_chunks(encoded, CHUNK_SIZE))
        version = self.expected_version
        md5 = hashlib.md5(archive, usedforsecurity=False).hexdigest()
        sha256 = hashlib.sha256(archive).hexdigest()

        snapshot = self._capture_install_snapshot(new_package_count=len(chunks))
        if not _persisted_core_boot_is_valid(snapshot.values):
            raise RouterError(
                "companion installation was refused because the captured "
                "persistent core snapshot is not reboot-valid and therefore "
                "cannot be used for safe rollback"
            )
        rollback_target = _validate_rollback_target(snapshot)
        old_installed = snapshot.values["astrill_lazy_installed"] == "1"
        startup = snapshot.values["rc_startup"]
        pages = snapshot.values["mypage_scripts"].split()
        if not old_installed:
            previous_startup = startup
            previous_pages = " ".join(pages)
        else:
            previous_startup = snapshot.values["astrill_lazy_previous_rc_startup"]
            previous_pages = snapshot.values["astrill_lazy_previous_mypage_scripts"]
        stored_rules = {
            key: snapshot.values[key] for pair in RULE_STORAGE_PAIRS for key in pair
        }

        startup = _without_companion_startup_lines(startup)
        if STARTUP_LINE not in startup.splitlines():
            startup = f"{startup.rstrip()}\n{STARTUP_LINE}".lstrip()
        for command in PAGE_COMMANDS:
            if command not in pages:
                pages.append(command)

        bootstrap = _stored_bootstrap(self.router_root)
        bootstrap_bytes = (bootstrap + "\n").encode("ascii")
        bootstrap_md5 = hashlib.md5(
            bootstrap_bytes,
            usedforsecurity=False,
        ).hexdigest()
        assignments: list[tuple[str, str]] = [
            ("astrill_lazy_installed", "1"),
            ("astrill_lazy_version", version),
            ("astrill_lazy_pkg_count", str(len(chunks))),
            ("astrill_lazy_pkg_md5", md5),
            ("astrill_lazy_bootstrap", bootstrap),
            ("astrill_lazy_bootstrap_md5", bootstrap_md5),
            ("astrill_lazy_previous_rc_startup", previous_startup),
            ("astrill_lazy_previous_mypage_scripts", previous_pages),
            ("rc_startup", startup),
            ("mypage_scripts", " ".join(pages)),
        ]
        assignments.extend(
            (f"astrill_lazy_pkg_{index}", chunk) for index, chunk in enumerate(chunks)
        )
        expected_install, expected_install_present = self._expected_install_state(
            snapshot,
            assignments,
            new_package_count=len(chunks),
        )
        growth, projected_free = self._project_install_headroom(
            snapshot,
            expected_install,
            expected_install_present,
        )
        return _PreparedInstall(
            archive=archive,
            chunks=chunks,
            version=version,
            package_md5=md5,
            package_sha256=sha256,
            snapshot=snapshot,
            rollback_target=rollback_target,
            pages=tuple(pages),
            assignments=tuple(assignments),
            expected_install=expected_install,
            expected_install_present=expected_install_present,
            stored_rules=stored_rules,
            projected_growth=growth,
            projected_free=projected_free,
        )

    def install(self) -> InstallResult:
        prepared = self._prepare_install()
        if prepared.projected_free < MIN_NVRAM_FREE_BYTES:
            raise RouterError(
                "insufficient NVRAM headroom for companion installation: "
                f"{prepared.projected_free} bytes would remain after projected "
                f"{prepared.projected_growth:+d}-byte growth; at least "
                f"{MIN_NVRAM_FREE_BYTES} bytes must remain"
            )
        snapshot = prepared.snapshot
        chunks = prepared.chunks
        version = prepared.version
        sha256 = prepared.package_sha256
        rollback_target = prepared.rollback_target
        pages = list(prepared.pages)
        assignments = list(prepared.assignments)
        expected_install = prepared.expected_install
        expected_install_present = prepared.expected_install_present
        stored_rules = prepared.stored_rules
        growth = prepared.projected_growth
        old_count = snapshot.package_count
        script = self._install_transaction_script(
            snapshot,
            expected_install,
            expected_install_present,
            assignments,
            stored_rules=stored_rules,
            old_package_count=old_count,
            new_package_count=len(chunks),
            projected_growth=growth,
        )
        try:
            self.client.run_script(script, timeout=300)
            self._validate_persisted_core()
            status = self.client.status()
            if not self._runtime_is_current(status):
                raise RouterError(
                    "router package installed but its controller, watchdog, or "
                    "policy routing is not ready"
                )
        except (Exception, KeyboardInterrupt) as exc:
            failure = str(exc).strip() or type(exc).__name__
            if INSTALL_PRECONDITION_ERROR in failure:
                raise RouterError(
                    "router companion installation was refused before any "
                    f"NVRAM mutation: {failure}"
                ) from exc
            recovered, recovery_detail = self._recover_failed_install(
                snapshot,
                expected_install=expected_install,
                expected_install_present=expected_install_present,
                rollback_target=rollback_target,
            )
            recovery_state = (
                "Recovery verified" if recovered else "Recovery was not verified"
            )
            if isinstance(exc, KeyboardInterrupt):
                exc.add_note(f"{recovery_state}: {recovery_detail}")
                raise
            raise RouterError(
                f"router companion installation failed: {failure}. "
                f"{recovery_state}: {recovery_detail}"
            ) from exc

        policy_page = pages.index(PAGE_COMMANDS[0]) + 1
        api_page = pages.index(PAGE_COMMANDS[1]) + 1
        return InstallResult(
            version=version,
            package_bytes=len(prepared.archive),
            package_sha256=sha256,
            nvram_chunks=len(chunks),
            policy_page=policy_page,
            api_page=api_page,
            status=status,
        )

    def _capture_install_snapshot(
        self,
        *,
        new_package_count: int = 0,
        package_indices: Iterable[int] = (),
        capture_headroom: bool = True,
    ) -> _InstallSnapshot:
        count_value = self._nvram_get_exact("astrill_lazy_pkg_count")
        present = set()
        if self._nvram_is_set("astrill_lazy_pkg_count"):
            present.add("astrill_lazy_pkg_count")
        else:
            count_value = ""
        package_count = _integer(count_value)
        if package_count > MAX_PACKAGE_CHUNKS or new_package_count > MAX_PACKAGE_CHUNKS:
            raise RouterError(
                "companion package chunk count is outside the safe NVRAM range"
            )
        values = {"astrill_lazy_pkg_count": count_value}
        snapshot_keys = (
            *(key for key in PACKAGE_METADATA_KEYS if key != "astrill_lazy_pkg_count"),
            *(key for pair in RULE_STORAGE_PAIRS for key in pair),
            *STARTUP_METADATA_KEYS,
        )
        indices = set(range(max(package_count, new_package_count)))
        indices.update(package_indices)
        if any(index < 0 or index >= MAX_PACKAGE_CHUNKS for index in indices):
            raise RouterError(
                "companion package chunk index is outside the safe NVRAM range"
            )
        keys = [
            *snapshot_keys,
            *(f"astrill_lazy_pkg_{index}" for index in sorted(indices)),
        ]
        for key in keys:
            value = self._nvram_get_exact(key)
            if self._nvram_is_set(key):
                present.add(key)
                values[key] = value
            else:
                values[key] = ""
        return _InstallSnapshot(
            values=values,
            present=frozenset(present),
            package_count=package_count,
            nvram_free_bytes=self._nvram_free_bytes() if capture_headroom else 0,
        )

    def _package_chunk_indices(self) -> tuple[int, ...]:
        output = self.client.run_script(
            """
nvram show 2>/dev/null |
    sed -n 's/^astrill_lazy_pkg_\\([0-9][0-9]*\\)=.*/\\1/p' |
    sort -n -u
""",
            timeout=30,
        )
        indices: list[int] = []
        for line in output.splitlines():
            value = line.strip()
            if not re.fullmatch(r"[0-9]+", value):
                raise RouterError("could not enumerate companion package chunks")
            index = int(value)
            if index >= MAX_PACKAGE_CHUNKS:
                raise RouterError(
                    "companion package chunk index is outside the safe NVRAM range"
                )
            indices.append(index)
        return tuple(indices)

    def _nvram_free_bytes(self) -> int:
        output = self.client.run_script(
            """
set -e
size_line=$(nvram show 2>&1 >/dev/null)
free_bytes=$(printf '%s\\n' "$size_line" |
    sed -n 's/.*(\\([0-9][0-9]*\\) left).*/\\1/p')
case $free_bytes in ''|*[!0-9]*) exit 1 ;; esac
printf '%s\\n' "$free_bytes"
""",
            timeout=30,
        )
        value = output.strip()
        if not re.fullmatch(r"[0-9]+", value):
            raise RouterError("could not determine conservative NVRAM headroom")
        return int(value)

    def _preflight_install(
        self,
        snapshot: _InstallSnapshot,
        expected_install: dict[str, str],
        expected_install_present: frozenset[str],
    ) -> int:
        growth, projected_free = self._project_install_headroom(
            snapshot,
            expected_install,
            expected_install_present,
        )
        if projected_free < MIN_NVRAM_FREE_BYTES:
            raise RouterError(
                "insufficient NVRAM headroom for companion installation: "
                f"{projected_free} bytes would remain after projected "
                f"{growth:+d}-byte growth; at least "
                f"{MIN_NVRAM_FREE_BYTES} bytes must remain"
            )
        return growth

    @staticmethod
    def _project_install_headroom(
        snapshot: _InstallSnapshot,
        expected_install: dict[str, str],
        expected_install_present: frozenset[str],
    ) -> tuple[int, int]:
        current = {key: snapshot.values[key] for key in snapshot.present}
        desired = {key: expected_install[key] for key in expected_install_present}

        current_bytes = sum(
            _nvram_entry_bytes(key, value) for key, value in current.items()
        )
        desired_bytes = sum(
            _nvram_entry_bytes(key, value) for key, value in desired.items()
        )
        growth = desired_bytes - current_bytes
        projected_free = snapshot.nvram_free_bytes - growth
        return growth, projected_free

    @staticmethod
    def _expected_install_state(
        snapshot: _InstallSnapshot,
        assignments: list[tuple[str, str]],
        *,
        new_package_count: int,
    ) -> tuple[dict[str, str], frozenset[str]]:
        expected = dict(snapshot.values)
        expected.update(_projected_rule_storage(snapshot.values))
        present = _projected_rule_presence(snapshot.values, snapshot.present)
        expected.update(assignments)
        present.update(key for key, _value in assignments)
        for index in range(new_package_count, snapshot.package_count):
            expected[f"astrill_lazy_pkg_{index}"] = ""
            present.discard(f"astrill_lazy_pkg_{index}")
        return expected, frozenset(present)

    def _install_transaction_script(
        self,
        snapshot: _InstallSnapshot,
        expected_install: dict[str, str],
        expected_install_present: frozenset[str],
        assignments: list[tuple[str, str]],
        *,
        stored_rules: dict[str, str],
        old_package_count: int,
        new_package_count: int,
        projected_growth: int,
    ) -> str:
        mutation_commands = _rule_storage_migration_commands(stored_rules)
        mutation_commands.extend(
            _nvram_set_command(key, value) for key, value in assignments
        )
        mutation_commands.extend(
            f"nvram unset {shlex.quote(f'astrill_lazy_pkg_{index}')}"
            for index in range(new_package_count, old_package_count)
        )
        mutation_commands.append("nvram commit >/dev/null")

        script = [
            "mkdir -p /tmp/astrill-lazy || exit 1",
            *_controller_lock_shell(),
            *_nvram_free_shell(),
            *_nvram_assert_function(
                "snapshot_matches",
                snapshot.values,
                snapshot.present,
            ),
            *_nvram_assert_function(
                "installed_matches",
                expected_install,
                expected_install_present,
            ),
            *_nvram_mutation_function("install_nvram", mutation_commands),
            *_nvram_restore_function(
                "restore_snapshot",
                snapshot.values,
                snapshot.present,
            ),
            "transaction_active=0",
            "install_cleanup() {",
            "    cleanup_status=$?",
            "    trap - EXIT INT TERM HUP",
            '    if [ "$transaction_active" -eq 1 ]; then',
            "        restore_snapshot >/dev/null 2>&1 || true",
            "    fi",
            '    rm -f "$NVRAM_PRESENCE_FILE" "/tmp/astrill-lazy-nvram-cas.$$"',
            '    rm -f "$LOCK_DIR/pid"',
            '    rmdir "$LOCK_DIR" 2>/dev/null || true',
            '    [ "$cleanup_status" -ne 0 ] || cleanup_status=79',
            '    exit "$cleanup_status"',
            "}",
            (
                "acquire_lock || { printf '%s\\n' "
                f"{shlex.quote(INSTALL_PRECONDITION_ERROR + ' controller is busy')} "
                ">&2; exit 75; }"
            ),
            (
                "refresh_nvram_presence || { "
                f"printf '%s\\n' {shlex.quote(INSTALL_PRECONDITION_ERROR + ' could not capture NVRAM presence')} "
                ">&2; release_lock; exit 75; }"
            ),
        ]
        for key, expected in sorted(snapshot.values.items()):
            expected_present = "1" if key in snapshot.present else "0"
            script.append(
                "assert_nvram "
                f"{shlex.quote(key)} "
                f"{shlex.quote(_nvram_value_hex(expected))} "
                f"{expected_present} || {{ "
                f"printf '%s %s\\n' {shlex.quote(INSTALL_PRECONDITION_ERROR)} "
                f"{shlex.quote('NVRAM changed at ' + key)} >&2; "
                "release_lock; exit 75; }"
            )
        script.extend(
            [
                (
                    "[ ! -e /tmp/astrill-lazy/policy-transaction ] || { "
                    f"printf '%s\\n' {shlex.quote(INSTALL_PRECONDITION_ERROR + ' pending policy transaction requires recovery')} "
                    ">&2; release_lock; exit 75; }"
                ),
                (
                    "free_bytes=$(nvram_free_bytes) || { "
                    f"printf '%s\\n' {shlex.quote(INSTALL_PRECONDITION_ERROR + ' could not recheck NVRAM headroom')} "
                    ">&2; release_lock; exit 75; }"
                ),
                f"projected_free=$((free_bytes - ({projected_growth})))",
                (
                    f'[ "$projected_free" -ge {MIN_NVRAM_FREE_BYTES} ] || {{ '
                    f"printf '%s %s\\n' {shlex.quote(INSTALL_PRECONDITION_ERROR)} "
                    '"live NVRAM headroom shrank" >&2; '
                    "release_lock; exit 75; }"
                ),
                "transaction_active=1",
                "trap install_cleanup EXIT INT TERM HUP",
                "if ! install_nvram || ! installed_matches; then",
                "    if restore_snapshot && snapshot_matches; then",
                "        transaction_active=0",
                "        release_lock",
                (
                    "        printf '%s\\n' "
                    "'astrill-lazy installer mutation failed; "
                    "original NVRAM restored in the same session' >&2"
                ),
                "        exit 77",
                "    fi",
                "    transaction_active=0",
                "    release_lock",
                (
                    "    printf '%s\\n' "
                    "'astrill-lazy installer mutation failed and exact "
                    "same-session restoration could not be verified' >&2"
                ),
                "    exit 78",
                "fi",
                "transaction_active=0",
                "release_lock",
                # bootstrap.sh waits on the same controller lock. Holding it
                # here would deadlock an otherwise successful installation.
                *_bootstrap_shell("installed"),
            ]
        )
        return "\n".join(script) + "\n"

    def _recover_failed_install(
        self,
        snapshot: _InstallSnapshot,
        *,
        expected_install: dict[str, str],
        expected_install_present: frozenset[str],
        rollback_target: _RollbackTarget | None = None,
    ) -> tuple[bool, str]:
        old_installed = snapshot.values["astrill_lazy_installed"] == "1"
        if not old_installed:
            return self._recover_failed_install_to_uninstalled(
                snapshot,
                expected_install=expected_install,
                expected_install_present=expected_install_present,
            )
        script = [
            "mkdir -p /tmp/astrill-lazy || exit 1",
            *_controller_lock_shell(),
            *_nvram_assert_function(
                "snapshot_matches",
                snapshot.values,
                snapshot.present,
            ),
            *_nvram_assert_function(
                "installed_matches",
                expected_install,
                expected_install_present,
            ),
            *_nvram_restore_function(
                "restore_snapshot",
                snapshot.values,
                snapshot.present,
            ),
            *_nvram_restore_function(
                "restore_installed",
                expected_install,
                expected_install_present,
            ),
            "recovery_active=0",
            "recovery_cleanup() {",
            "    cleanup_status=$?",
            "    trap - EXIT INT TERM HUP",
            '    if [ "$recovery_active" -eq 1 ]; then',
            "        restore_installed >/dev/null 2>&1 || true",
            "    fi",
            '    rm -f "$NVRAM_PRESENCE_FILE" "/tmp/astrill-lazy-nvram-cas.$$"',
            '    rm -f "$LOCK_DIR/pid"',
            '    rmdir "$LOCK_DIR" 2>/dev/null || true',
            '    [ "$cleanup_status" -ne 0 ] || cleanup_status=79',
            '    exit "$cleanup_status"',
            "}",
            (
                "acquire_lock || { printf '%s\\n' "
                "'astrill-lazy installer recovery could not acquire the "
                "controller lock' >&2; exit 75; }"
            ),
            "if snapshot_matches; then",
            "    recovery_active=1",
            "    trap recovery_cleanup EXIT INT TERM HUP",
            "    if ! nvram commit >/dev/null || ! snapshot_matches; then",
            ("        restore_installed >/dev/null 2>&1 && installed_matches || true"),
            "        recovery_active=0",
            "        release_lock",
            (
                "        printf '%s\\n' 'astrill-lazy installer recovery "
                "could not commit and verify the exact snapshot' >&2"
            ),
            "        exit 78",
            "    fi",
            "    recovery_active=0",
            "elif installed_matches; then",
            "    recovery_active=1",
            "    trap recovery_cleanup EXIT INT TERM HUP",
            "    if ! restore_snapshot || ! snapshot_matches; then",
            ("        restore_installed >/dev/null 2>&1 && installed_matches || true"),
            "        recovery_active=0",
            "        release_lock",
            (
                "        printf '%s\\n' 'astrill-lazy installer recovery "
                "could not restore the exact snapshot' >&2"
            ),
            "        exit 78",
            "    fi",
            "    recovery_active=0",
            "else",
            "    release_lock",
            (
                "    printf '%s\\n' 'astrill-lazy installer recovery refused: "
                "a newer policy, package, or startup state is present' >&2"
            ),
            "    exit 76",
            "fi",
            "release_lock",
        ]
        if rollback_target is None:
            try:
                rollback_target = _validate_rollback_target(snapshot)
            except RouterError as exc:
                return False, f"rollback target is not reboot-valid: {exc}"
        if rollback_target is None:
            return False, "rollback target unexpectedly has no companion package"
        trusted_bootstrap = _normalized_bootstrap(self.router_root).decode("ascii")
        script.extend(
            _snapshot_bootstrap_shell(
                snapshot,
                rollback_target,
                trusted_bootstrap=trusted_bootstrap.rstrip("\n"),
            )
        )

        try:
            self.client.run_script("\n".join(script) + "\n", timeout=300)
            mismatched = [
                key
                for key, expected in snapshot.values.items()
                if (
                    self._nvram_get_exact(key) != expected
                    or self._nvram_is_set(key) != (key in snapshot.present)
                )
            ]
            if mismatched:
                return (
                    False,
                    (
                        "the previous NVRAM snapshot did not read back exactly "
                        f"({', '.join(sorted(mismatched))})"
                    ),
                )
            old_version = snapshot.values["astrill_lazy_version"]
            old_package_md5 = snapshot.values["astrill_lazy_pkg_md5"]
            if snapshot.values["astrill_lazy_pkg_md5"] == self.expected_package_md5:
                self._validate_persisted_core()
            status = self.client.status()
            allow_missing_package_md5 = False
            if (
                rollback_target is not None
                and not rollback_target.status_has_package_md5
                and not status.get("package_md5")
            ):
                allow_missing_package_md5 = self._runtime_files_match(rollback_target)
            if not self._runtime_is_healthy_version(
                status,
                old_version,
                old_package_md5,
                allow_missing_package_md5=allow_missing_package_md5,
            ):
                return (
                    False,
                    (
                        f"package {old_version or 'unknown'} was restored, but "
                        "its runtime is not healthy"
                    ),
                )
            return (
                True,
                f"the previous companion {old_version} was restored and is healthy",
            )
        # Recovery must never replace the initiating install failure.
        except Exception as recovery_exc:  # noqa: BLE001
            detail = str(recovery_exc).strip() or type(recovery_exc).__name__
            return False, f"rollback failed: {detail}"

    def _recover_failed_install_to_uninstalled(
        self,
        snapshot: _InstallSnapshot,
        *,
        expected_install: dict[str, str],
        expected_install_present: frozenset[str],
    ) -> tuple[bool, str]:
        snapshot_chunks = _present_package_chunk_list(snapshot.present)
        installed_chunks = _present_package_chunk_list(expected_install_present)
        script = [
            "BASE=/tmp/astrill-lazy",
            'mkdir -p "$BASE" || exit 1',
            *_controller_lock_shell(),
            *_nvram_assert_function(
                "snapshot_matches",
                snapshot.values,
                snapshot.present,
            ),
            *_nvram_assert_function(
                "installed_matches",
                expected_install,
                expected_install_present,
            ),
            *_nvram_restore_function(
                "restore_snapshot",
                snapshot.values,
                snapshot.present,
            ),
            *_uninstall_residue_functions(snapshot_chunks),
            *_nvram_chunk_set_function(
                "installed_chunks_match",
                installed_chunks,
            ),
            "if [ -x /tmp/astrill-lazy/alctl ]; then",
            (
                "    /tmp/astrill-lazy/alctl stop >/dev/null 2>&1 || { "
                "printf '%s\\n' 'astrill-lazy installer recovery could not "
                "stop the attempted runtime' >&2; exit 80; }"
            ),
            "fi",
            "recovery_active=0",
            "recovery_cleanup() {",
            "    cleanup_status=$?",
            "    trap - EXIT INT TERM HUP",
            '    if [ "$recovery_active" -eq 1 ]; then',
            "        restore_snapshot >/dev/null 2>&1 || true",
            "    fi",
            '    rm -f "$NVRAM_PRESENCE_FILE" "/tmp/astrill-lazy-nvram-cas.$$"',
            '    rm -f "$LOCK_DIR/pid"',
            '    rmdir "$LOCK_DIR" 2>/dev/null || true',
            '    [ "$cleanup_status" -ne 0 ] || cleanup_status=79',
            '    exit "$cleanup_status"',
            "}",
            (
                "acquire_lock || { printf '%s\\n' 'astrill-lazy installer "
                "recovery could not acquire the controller lock' >&2; "
                "exit 75; }"
            ),
            "if snapshot_matches && snapshot_chunks_match; then",
            "    recovery_source=snapshot",
            "elif installed_matches && installed_chunks_match; then",
            "    recovery_source=installed",
            "else",
            "    release_lock",
            (
                "    printf '%s\\n' 'astrill-lazy installer recovery refused: "
                "a newer policy, package, startup, or package-chunk state is "
                "present' >&2"
            ),
            "    exit 76",
            "fi",
            "if ! runtime_is_quiescent; then",
            "    release_lock",
            (
                "    printf '%s\\n' 'astrill-lazy installer recovery refused: "
                "policy runtime residue returned after stop' >&2"
            ),
            "    exit 76",
            "fi",
            "recovery_active=1",
            "trap recovery_cleanup EXIT INT TERM HUP",
            "recovery_failed=0",
            'if [ "$recovery_source" = snapshot ]; then',
            (
                "    nvram commit >/dev/null && snapshot_matches && "
                "snapshot_chunks_match || recovery_failed=1"
            ),
            "else",
            (
                "    restore_snapshot && snapshot_matches && "
                "snapshot_chunks_match || recovery_failed=1"
            ),
            "fi",
            'if [ "$recovery_failed" -eq 0 ]; then',
            (
                "    cleanup_runtime_files && runtime_is_quiescent && "
                "snapshot_matches && snapshot_chunks_match || recovery_failed=1"
            ),
            "fi",
            'if [ "$recovery_failed" -ne 0 ]; then',
            (
                "    if restore_snapshot && snapshot_matches && "
                "snapshot_chunks_match; then"
            ),
            "        recovery_active=0",
            "        release_lock",
            (
                "        printf '%s\\n' 'astrill-lazy installer recovery "
                "failed; the exact uninstalled NVRAM snapshot was restored "
                "in the same session' >&2"
            ),
            "        exit 77",
            "    fi",
            "    recovery_active=0",
            "    release_lock",
            (
                "    printf '%s\\n' 'astrill-lazy installer recovery failed "
                "and exact uninstalled NVRAM restoration could not be "
                "verified' >&2"
            ),
            "    exit 78",
            "fi",
            "recovery_active=0",
            "release_lock",
            'rmdir "$BASE" 2>/dev/null || true',
        ]
        try:
            self.client.run_script("\n".join(script) + "\n", timeout=300)
            mismatched = [
                key
                for key, expected in snapshot.values.items()
                if (
                    self._nvram_get_exact(key) != expected
                    or self._nvram_is_set(key) != (key in snapshot.present)
                )
            ]
            if mismatched:
                return (
                    False,
                    (
                        "the previous NVRAM snapshot did not read back exactly "
                        f"({', '.join(sorted(mismatched))})"
                    ),
                )
            return True, "the previous uninstalled state was restored"
        except Exception as recovery_exc:  # noqa: BLE001
            detail = str(recovery_exc).strip() or type(recovery_exc).__name__
            return False, f"rollback failed: {detail}"

    def _validate_persisted_core(self) -> None:
        self.client.raw(
            [
                "/tmp/astrill-lazy/alctl",
                "validate-persisted-core",
                "--json",
            ],
            timeout=120,
        )

    @staticmethod
    def _runtime_is_healthy_version(
        status: dict[str, Any],
        version: str,
        package_md5: str,
        *,
        allow_missing_package_md5: bool = False,
    ) -> bool:
        runtime_package_md5 = status.get("package_md5")
        return (
            status.get("version") == version
            and (
                not package_md5
                or runtime_package_md5 == package_md5
                or (allow_missing_package_md5 and not runtime_package_md5)
            )
            and status.get("jump_installed") is True
            and status.get("watchdog") is True
            and status.get("policy_health") == "ready"
            and status.get("precedence_ok") is True
        )

    def _runtime_files_match(self, rollback_target: _RollbackTarget) -> bool:
        commands = ["set -e"]
        for name, expected_md5 in rollback_target.runtime_md5:
            path = shlex.quote(f"/tmp/astrill-lazy/{name}")
            commands.extend(
                [
                    f"[ -f {path} ]",
                    (
                        f"actual=$(md5sum {path} | awk '{{print $1}}') && "
                        f'[ "$actual" = {shlex.quote(expected_md5)} ]'
                    ),
                ]
            )
        try:
            self.client.run_script("\n".join(commands) + "\n", timeout=45)
        except RouterError:
            return False
        return True

    def uninstall(self) -> dict[str, Any]:
        before = self.client.native_astrill_status()
        self.client.run_script(
            """if [ -x /tmp/astrill-lazy/alctl ]; then
    /tmp/astrill-lazy/alctl stop || {
        printf '%s\n' 'astrill-lazy uninstaller could not stop the policy runtime' >&2
        exit 80
    }
fi
""",
            timeout=75,
        )
        package_indices = self._package_chunk_indices()
        snapshot = self._capture_install_snapshot(
            package_indices=package_indices,
            capture_headroom=False,
        )
        startup = _without_companion_startup_lines(
            snapshot.values["rc_startup"]
        ).strip()
        pages = [
            value
            for value in snapshot.values["mypage_scripts"].split()
            if value not in PAGE_COMMANDS
        ]
        expected_uninstall = dict(snapshot.values)
        expected_uninstall_present = set(snapshot.present)
        expected_uninstall["rc_startup"] = startup
        expected_uninstall["mypage_scripts"] = " ".join(pages)
        expected_uninstall_present.update(("rc_startup", "mypage_scripts"))
        owned_keys = {
            *PACKAGE_METADATA_KEYS,
            *(key for pair in RULE_STORAGE_PAIRS for key in pair),
            *(
                key
                for key in STARTUP_METADATA_KEYS
                if key not in {"rc_startup", "mypage_scripts"}
            ),
            *(
                key
                for key in snapshot.values
                if key.startswith("astrill_lazy_pkg_")
                and key.removeprefix("astrill_lazy_pkg_").isdigit()
            ),
        }
        for key in owned_keys:
            expected_uninstall[key] = ""
            expected_uninstall_present.discard(key)

        script = self._uninstall_transaction_script(
            snapshot,
            expected_uninstall,
            frozenset(expected_uninstall_present),
            owned_keys=owned_keys,
            startup=startup,
            pages=" ".join(pages),
        )
        try:
            self.client.run_script(script, timeout=300)
        except (Exception, KeyboardInterrupt) as exc:
            failure = str(exc).strip() or type(exc).__name__
            if isinstance(exc, KeyboardInterrupt):
                exc.add_note(
                    "The locked uninstall transaction attempted exact "
                    "same-session NVRAM restoration."
                )
                raise
            if UNINSTALL_PRECONDITION_ERROR in failure:
                raise RouterError(
                    "router companion removal was refused before any NVRAM "
                    f"mutation: {failure}"
                ) from exc
            raise RouterError(
                "router companion removal failed; the locked transaction "
                f"attempted exact NVRAM restoration: {failure}"
            ) from exc

        mismatched = [
            key
            for key, expected in expected_uninstall.items()
            if (
                self._nvram_get_exact(key) != expected
                or self._nvram_is_set(key) != (key in expected_uninstall_present)
            )
        ]
        remaining_chunks = self._package_chunk_indices()
        if mismatched or remaining_chunks:
            details = sorted(mismatched)
            details.extend(f"astrill_lazy_pkg_{index}" for index in remaining_chunks)
            raise RouterError(
                "the companion removal did not read back the exact "
                "uninstalled NVRAM state: " + ", ".join(details)
            )

        after = self.client.native_astrill_status()
        preserved_fields = ("vpn_state", "astrill_server_id", "astrill_protocol")
        changed = [
            field for field in preserved_fields if before.get(field) != after.get(field)
        ]
        if changed:
            raise RouterError(
                "the companion was removed, but native Astrill state changed: "
                + ", ".join(changed)
            )
        return after

    def _uninstall_transaction_script(
        self,
        snapshot: _InstallSnapshot,
        expected_uninstall: dict[str, str],
        expected_uninstall_present: frozenset[str],
        *,
        owned_keys: set[str],
        startup: str,
        pages: str,
    ) -> str:
        mutation_commands = [
            _nvram_set_command("rc_startup", startup),
            _nvram_set_command("mypage_scripts", pages),
            *(f"nvram unset {shlex.quote(key)}" for key in sorted(owned_keys)),
            "nvram commit >/dev/null",
        ]
        expected_chunk_list = _present_package_chunk_list(snapshot.present)
        script = [
            "BASE=/tmp/astrill-lazy",
            'mkdir -p "$BASE" || exit 1',
            *_controller_lock_shell(),
            *_nvram_assert_function(
                "snapshot_matches",
                snapshot.values,
                snapshot.present,
            ),
            *_nvram_assert_function(
                "uninstalled_nvram_matches",
                expected_uninstall,
                expected_uninstall_present,
            ),
            *_nvram_mutation_function("uninstall_nvram", mutation_commands),
            *_nvram_restore_function(
                "restore_snapshot",
                snapshot.values,
                snapshot.present,
            ),
            *_uninstall_residue_functions(expected_chunk_list),
            "transaction_active=0",
            "uninstall_cleanup() {",
            "    cleanup_status=$?",
            "    trap - EXIT INT TERM HUP",
            '    if [ "$transaction_active" -eq 1 ]; then',
            "        restore_snapshot >/dev/null 2>&1 || true",
            "    fi",
            '    rm -f "$NVRAM_PRESENCE_FILE" "/tmp/astrill-lazy-nvram-cas.$$"',
            '    rm -f "$LOCK_DIR/pid"',
            '    rmdir "$LOCK_DIR" 2>/dev/null || true',
            '    [ "$cleanup_status" -ne 0 ] || cleanup_status=79',
            '    exit "$cleanup_status"',
            "}",
            (
                "acquire_lock || { printf '%s\\n' "
                f"{shlex.quote(UNINSTALL_PRECONDITION_ERROR + ' controller is busy')} "
                ">&2; exit 75; }"
            ),
            (
                "if ! snapshot_matches || ! snapshot_chunks_match; then "
                f"printf '%s\\n' {shlex.quote(UNINSTALL_PRECONDITION_ERROR + ' NVRAM changed after runtime stop')} "
                ">&2; release_lock; exit 75; fi"
            ),
            (
                "if ! runtime_is_quiescent; then "
                f"printf '%s\\n' {shlex.quote(UNINSTALL_PRECONDITION_ERROR + ' policy runtime residue returned after stop')} "
                ">&2; release_lock; exit 75; fi"
            ),
            "transaction_active=1",
            "trap uninstall_cleanup EXIT INT TERM HUP",
            (
                "if ! uninstall_nvram || ! uninstalled_state_matches || "
                "! cleanup_runtime_files || ! runtime_is_quiescent; then"
            ),
            (
                "    if restore_snapshot && snapshot_matches && "
                "snapshot_chunks_match; then"
            ),
            "        transaction_active=0",
            "        release_lock",
            (
                "        printf '%s\\n' 'astrill-lazy uninstaller failed; "
                "the exact NVRAM snapshot was restored in the same session' >&2"
            ),
            "        exit 77",
            "    fi",
            "    transaction_active=0",
            "    release_lock",
            (
                "    printf '%s\\n' 'astrill-lazy uninstaller failed and "
                "exact NVRAM restoration could not be verified' >&2"
            ),
            "    exit 78",
            "fi",
            "transaction_active=0",
            "release_lock",
            # This is deliberately non-recursive. A concurrent controller
            # user that recreated anything under BASE wins the race safely.
            'rmdir "$BASE" 2>/dev/null || true',
        ]
        return "\n".join(script) + "\n"

    def _nvram_get(self, key: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
            raise ValueError(f"invalid NVRAM key: {key!r}")
        output = self.client.raw(["nvram", "get", key])
        return output.removesuffix("\n")

    def _nvram_get_exact(self, key: str) -> str:
        return self.client.nvram_get_exact(key)

    def _nvram_is_set(self, key: str) -> bool:
        return self.client.nvram_is_set(key)


def find_router_root() -> Path:
    package_file = Path(__file__).resolve()
    frozen_root = getattr(sys, "_MEIPASS", None)
    candidates = (
        *((Path(frozen_root) / "router",) if frozen_root else ()),
        package_file.parents[2] / "router",
        Path(sys.prefix) / "share" / "astrill-lazy" / "router",
        Path("/usr/local/share/astrill-lazy/router"),
        Path.home() / ".local" / "share" / "astrill-lazy" / "router",
    )
    for candidate in candidates:
        if all(
            (candidate / name).is_file() for name in (*PACKAGE_FILES, "bootstrap.sh")
        ):
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"router package files were not found in: {searched}")


def build_router_package(root: Path) -> bytes:
    output = BytesIO()
    with (
        gzip.GzipFile(
            fileobj=output, mode="wb", compresslevel=9, mtime=0
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name in PACKAGE_FILES:
            path = root / name
            data = path.read_bytes()
            if name == "VERSION":
                data = data.decode("ascii").strip().encode("ascii") + b"\n"
            info = tarfile.TarInfo(f"astrill-lazy/{name}")
            info.size = len(data)
            info.mode = 0o700 if name != "VERSION" else 0o600
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            info.mtime = 0
            archive.addfile(info, BytesIO(data))
    return output.getvalue()


def _normalized_bootstrap(root: Path) -> bytes:
    document = (root / "bootstrap.sh").read_text(encoding="ascii")
    return (document.rstrip("\n") + "\n").encode("ascii")


def _stored_bootstrap(root: Path) -> str:
    """Return the deterministic gzip/base64 bootstrap stored in NVRAM."""

    output = BytesIO()
    with gzip.GzipFile(
        fileobj=output,
        mode="wb",
        compresslevel=9,
        mtime=0,
    ) as compressed:
        compressed.write(_normalized_bootstrap(root))
    return base64.b64encode(output.getvalue()).decode("ascii")


def _canonical_stored_bootstrap(root: Path) -> bytes:
    """Return the canonical bytes hashed by the launcher and presence probe."""

    return (_stored_bootstrap(root) + "\n").encode("ascii")


def _decode_stored_bootstrap(value: str) -> bytes | None:
    """Decode one bounded gzip/base64 launcher payload, or reject its format."""

    try:
        encoded = value.strip().encode("ascii")
        archive = base64.b64decode(encoded, validate=True)
        if not archive or len(archive) > MAX_BOOTSTRAP_ARCHIVE_BYTES:
            return None
        with gzip.GzipFile(fileobj=BytesIO(archive), mode="rb") as compressed:
            document = compressed.read(MAX_BOOTSTRAP_EXPANDED_BYTES + 1)
        if len(document) > MAX_BOOTSTRAP_EXPANDED_BYTES:
            return None
        document.decode("ascii")
    except (EOFError, OSError, UnicodeError, ValueError, zlib.error):
        return None
    if not document.startswith(b"#!/bin/sh\n") or not document.strip():
        return None
    return document


def _is_uncompressed_bootstrap(value: str) -> bool:
    try:
        document = (value.rstrip("\n") + "\n").encode("ascii")
    except UnicodeError:
        return False
    return (
        len(document) <= MAX_BOOTSTRAP_EXPANDED_BYTES
        and document.startswith(b"#!/bin/sh\n")
        and bool(document.strip())
    )


def _chunks(value: str, size: int) -> Iterable[str]:
    for offset in range(0, len(value), size):
        yield value[offset : offset + size]


def _integer(value: str) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _nvram_set_command(key: str, value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_]+", key):
        raise ValueError(f"invalid NVRAM key: {key!r}")
    return f"nvram set {shlex.quote(f'{key}={value}')}"


def _compressed_rule_document(value: str) -> str:
    payload = gzip.compress(value.encode("ascii"), compresslevel=9, mtime=0)
    return base64.b64encode(payload).decode("ascii")


def _valid_compressed_rule_document(value: str) -> bool:
    try:
        payload = base64.b64decode(value, validate=True)
        document = gzip.decompress(payload).decode("ascii")
    except (EOFError, OSError, UnicodeError, ValueError, zlib.error):
        return False
    return document.startswith("# astrill-lazy-rules-v1")


def _rule_storage_migration_commands(values: dict[str, str]) -> list[str]:
    commands: list[str] = []
    for plain_key, compressed_key in RULE_STORAGE_PAIRS:
        plain_value = values.get(plain_key, "")
        compressed_value = values.get(compressed_key, "")
        if compressed_value and _valid_compressed_rule_document(compressed_value):
            if plain_value:
                commands.append(f"nvram unset {plain_key}")
            continue
        if not plain_value:
            continue
        try:
            candidate = _compressed_rule_document(plain_value)
        except UnicodeEncodeError:
            continue
        if len(candidate) >= len(plain_value.encode("ascii")):
            continue
        commands.append(_nvram_set_command(compressed_key, candidate))
        commands.append(f"nvram unset {plain_key}")
    return commands


def _projected_rule_storage(values: dict[str, str]) -> dict[str, str]:
    projected = {
        key: values.get(key, "") for pair in RULE_STORAGE_PAIRS for key in pair
    }
    for plain_key, compressed_key in RULE_STORAGE_PAIRS:
        plain_value = projected[plain_key]
        compressed_value = projected[compressed_key]
        if compressed_value and _valid_compressed_rule_document(compressed_value):
            projected[plain_key] = ""
            continue
        if not plain_value:
            continue
        try:
            candidate = _compressed_rule_document(plain_value)
            plain_bytes = len(plain_value.encode("ascii"))
        except UnicodeEncodeError:
            continue
        if len(candidate) >= plain_bytes:
            continue
        projected[compressed_key] = candidate
        projected[plain_key] = ""
    return projected


def _projected_rule_presence(
    values: dict[str, str],
    present: frozenset[str],
) -> set[str]:
    projected = set(present)
    for plain_key, compressed_key in RULE_STORAGE_PAIRS:
        plain_value = values.get(plain_key, "")
        compressed_value = values.get(compressed_key, "")
        if compressed_value and _valid_compressed_rule_document(compressed_value):
            if plain_value:
                projected.discard(plain_key)
            continue
        if not plain_value:
            continue
        try:
            candidate = _compressed_rule_document(plain_value)
        except UnicodeEncodeError:
            continue
        if len(candidate) >= len(plain_value.encode("ascii")):
            continue
        projected.add(compressed_key)
        projected.discard(plain_key)
    return projected


def _nvram_entry_bytes(key: str, value: str) -> int:
    # Broadcom NVRAM serializes each entry as ``name=value\0``.
    return len(key.encode("ascii")) + len(value.encode("utf-8")) + 2


def _nvram_value_hex(value: str) -> str:
    return value.encode("utf-8").hex()


def _persistent_hooks_are_current(startup: str, pages: str) -> bool:
    page_commands = pages.split()
    return STARTUP_LINE in startup.splitlines() and all(
        command in page_commands for command in PAGE_COMMANDS
    )


def _without_companion_startup_lines(startup: str) -> str:
    owned_lines = {
        STARTUP_LINE,
        UNCOMPRESSED_DIGEST_STARTUP_LINE,
        PREVIOUS_STARTUP_LINE,
        LEGACY_STARTUP_LINE,
    }
    return "\n".join(line for line in startup.splitlines() if line not in owned_lines)


def _bootstrap_shell(state: str) -> list[str]:
    label = shlex.quote(state)
    return [
        (
            "bootstrap_payload=$(nvram get astrill_lazy_bootstrap) || { "
            f"printf '%s %s\\n' 'astrill-lazy installer:' {label} "
            ">&2; exit 80; }"
        ),
        (
            "[ -n \"$(printf '%s' \"$bootstrap_payload\" | tr -d '[:space:]')\" ] "
            "|| { "
            f"printf '%s %s\\n' 'astrill-lazy installer: missing or empty "
            f"bootstrap after' {label} >&2; exit 80; }}"
        ),
        (
            "bootstrap_digest=$(nvram get astrill_lazy_bootstrap_md5) || { "
            f"printf '%s %s\\n' 'astrill-lazy installer: missing bootstrap "
            f"digest after' {label} >&2; exit 80; }}"
        ),
        f'case "$bootstrap_digest" in {MD5_SHELL_PATTERN}) ;;',
        (
            "    *) printf '%s %s\\n' 'astrill-lazy installer: invalid "
            f"bootstrap digest after' {label} >&2; exit 80 ;;"
        ),
        "esac",
        (
            "bootstrap_actual=$(printf '%s\\n' \"$bootstrap_payload\" | "
            "md5sum | awk '{print $1}') || { "
            f"printf '%s %s\\n' 'astrill-lazy installer: could not hash "
            f"bootstrap after' {label} >&2; exit 80; }}"
        ),
        (
            '[ "$bootstrap_actual" = "$bootstrap_digest" ] || { '
            f"printf '%s %s\\n' 'astrill-lazy installer: bootstrap digest "
            f"mismatch after' {label} >&2; exit 80; }}"
        ),
        (
            "bootstrap_script=$({ "
            "printf 'begin-base64 600 bootstrap.gz\\n'; "
            "printf '%s\\n' \"$bootstrap_payload\"; "
            "printf '====\\n'; "
            "} | uudecode -o - 2>/dev/null | gzip -dc 2>/dev/null) || { "
            f"printf '%s %s\\n' 'astrill-lazy installer: could not decode "
            f"bootstrap after' {label} >&2; exit 80; }}"
        ),
        (
            "[ -n \"$(printf '%s' \"$bootstrap_script\" | tr -d '[:space:]')\" ] "
            "|| { "
            f"printf '%s %s\\n' 'astrill-lazy installer: decoded an empty "
            f"bootstrap after' {label} >&2; exit 80; }}"
        ),
        (
            'ASTRILL_LAZY_BOOTSTRAP_MD5="$bootstrap_digest" '
            '/bin/sh -c "$bootstrap_script"'
        ),
    ]


def _snapshot_bootstrap_shell(
    snapshot: _InstallSnapshot,
    rollback_target: _RollbackTarget,
    *,
    trusted_bootstrap: str,
) -> list[str]:
    bootstrap_present = "1" if "astrill_lazy_bootstrap" in snapshot.present else "0"
    digest_present = "1" if "astrill_lazy_bootstrap_md5" in snapshot.present else "0"
    bootstrap_digest_value = snapshot.values.get(
        "astrill_lazy_bootstrap_md5",
        "",
    )
    return [
        (
            "refresh_nvram_presence && "
            "assert_nvram astrill_lazy_bootstrap "
            f"{shlex.quote(_nvram_value_hex(snapshot.values['astrill_lazy_bootstrap']))} "
            f"{bootstrap_present} && "
            "assert_nvram astrill_lazy_bootstrap_md5 "
            f"{shlex.quote(_nvram_value_hex(bootstrap_digest_value))} "
            f"{digest_present} || {{ printf '%s\\n' "
            "'astrill-lazy installer: restored bootstrap changed before "
            "launch' >&2; exit 80; }"
        ),
        (
            "ASTRILL_LAZY_RECOVERY=1 "
            "ASTRILL_LAZY_RECOVERY_VERSION="
            f"{shlex.quote(snapshot.values['astrill_lazy_version'])} "
            "ASTRILL_LAZY_RECOVERY_PACKAGE_MD5="
            f"{shlex.quote(snapshot.values['astrill_lazy_pkg_md5'])} "
            "ASTRILL_LAZY_RECOVERY_BOOTSTRAP_MD5="
            f"{shlex.quote(rollback_target.bootstrap_md5)} "
            f"/bin/sh -c {shlex.quote(trusted_bootstrap)}"
        ),
    ]


def _present_package_chunk_list(present: frozenset[str]) -> str:
    indices = sorted(
        int(key.removeprefix("astrill_lazy_pkg_"))
        for key in present
        if key.startswith("astrill_lazy_pkg_")
        and key.removeprefix("astrill_lazy_pkg_").isdigit()
    )
    return "\n".join(str(index) for index in indices)


def _nvram_chunk_set_function(name: str, expected_chunk_list: str) -> list[str]:
    expected_chunks = shlex.quote(expected_chunk_list)
    return [
        f"{name}() {{",
        "    refresh_nvram_presence || return 1",
        (
            "    current_chunks=$(sed -n "
            "'s/^astrill_lazy_pkg_\\([0-9][0-9]*\\)=.*/\\1/p' "
            '"$NVRAM_PRESENCE_FILE" | sort -n -u) || return 1'
        ),
        f'    [ "$current_chunks" = {expected_chunks} ]',
        "}",
    ]


def _uninstall_residue_functions(expected_chunk_list: str) -> list[str]:
    return [
        *_nvram_chunk_set_function(
            "snapshot_chunks_match",
            expected_chunk_list,
        ),
        "uninstalled_state_matches() {",
        "    uninstalled_nvram_matches || return 1",
        ("    ! grep -Eq '^astrill_lazy_pkg_[0-9][0-9]*=' \"$NVRAM_PRESENCE_FILE\""),
        "}",
        "runtime_is_quiescent() {",
        '    [ ! -e "$BASE/policy-transaction" ] || return 1',
        "    processes=$(ps w) || return 1",
        (
            "    ! printf '%s\\n' \"$processes\" | grep -F "
            "'/tmp/astrill-lazy/alctl watchdog-loop' | grep -vq grep "
            "|| return 1"
        ),
        ("    mangle_rules=$(iptables -w 10 -t mangle -S 2>/dev/null) || return 1"),
        ("    ! printf '%s\\n' \"$mangle_rules\" | grep -q 'AL_LAZY_' || return 1"),
        ("    filter_rules=$(iptables -w 10 -t filter -S 2>/dev/null) || return 1"),
        ("    ! printf '%s\\n' \"$filter_rules\" | grep -q 'AL_LAZY_' || return 1"),
        "    rpdb_rules=$(ip rule show) || return 1",
        (
            "    ! printf '%s\\n' \"$rpdb_rules\" | "
            "grep -Eq 'lookup (212|213)$' || return 1"
        ),
        "    routes_212=$(ip route show table 212 2>/dev/null) || return 1",
        '    [ -z "$routes_212" ] || return 1',
        "    routes_213=$(ip route show table 213 2>/dev/null) || return 1",
        '    [ -z "$routes_213" ] || return 1',
        "    return 0",
        "}",
        "cleanup_runtime_files() {",
        "    cleanup_failed=0",
        '    for runtime_path in "$BASE"/* "$BASE"/.[!.]* "$BASE"/..?*; do',
        '        [ -e "$runtime_path" ] || continue',
        '        [ "$runtime_path" = "$LOCK_DIR" ] && continue',
        '        rm -rf "$runtime_path" || cleanup_failed=1',
        "    done",
        '    [ "$cleanup_failed" -eq 0 ] || return 1',
        '    for runtime_path in "$BASE"/* "$BASE"/.[!.]* "$BASE"/..?*; do',
        '        [ -e "$runtime_path" ] || continue',
        '        [ "$runtime_path" = "$LOCK_DIR" ] && continue',
        "        return 1",
        "    done",
        "    return 0",
        "}",
    ]


def _validate_rollback_target(
    snapshot: _InstallSnapshot,
) -> _RollbackTarget | None:
    if snapshot.values.get("astrill_lazy_installed") != "1":
        return None

    required = {
        "astrill_lazy_installed",
        "astrill_lazy_version",
        "astrill_lazy_pkg_count",
        "astrill_lazy_pkg_md5",
        "astrill_lazy_bootstrap",
        "rc_startup",
    }
    missing = sorted(required - snapshot.present)
    if missing:
        raise RouterError(
            "captured installed rollback target is missing " + ", ".join(missing)
        )

    count_value = snapshot.values["astrill_lazy_pkg_count"]
    if not re.fullmatch(r"[1-9][0-9]*", count_value):
        raise RouterError(
            "captured installed rollback target has an invalid package count"
        )
    package_count = int(count_value)
    if package_count > MAX_PACKAGE_CHUNKS:
        raise RouterError(
            "captured installed rollback target has too many package chunks"
        )

    chunks: list[str] = []
    for index in range(package_count):
        key = f"astrill_lazy_pkg_{index}"
        if key not in snapshot.present or not snapshot.values.get(key):
            raise RouterError(
                f"captured installed rollback target is missing package chunk {index}"
            )
        chunks.append(snapshot.values[key])
    try:
        encoded = "".join(chunks).encode("ascii")
        archive = base64.b64decode(encoded, validate=True)
    except (UnicodeError, ValueError) as exc:
        raise RouterError(
            "captured installed rollback package is not strict base64"
        ) from exc
    if not archive or len(archive) > MAX_PACKAGE_ARCHIVE_BYTES:
        raise RouterError(
            "captured installed rollback package size is outside the safe range"
        )

    package_md5 = snapshot.values["astrill_lazy_pkg_md5"]
    if not re.fullmatch(r"[0-9a-f]{32}", package_md5):
        raise RouterError(
            "captured installed rollback target has an invalid package digest"
        )
    actual_package_md5 = hashlib.md5(
        archive,
        usedforsecurity=False,
    ).hexdigest()
    if actual_package_md5 != package_md5:
        raise RouterError("captured installed rollback package digest does not match")

    expected_names = {f"astrill-lazy/{name}" for name in PACKAGE_FILES}
    try:
        with tarfile.open(fileobj=BytesIO(archive), mode="r:gz") as package:
            members = package.getmembers()
            member_names = [member.name for member in members]
            if (
                len(member_names) != len(expected_names)
                or set(member_names) != expected_names
                or any(not member.isfile() for member in members)
                or any(member.size <= 0 for member in members)
                or sum(member.size for member in members) > MAX_PACKAGE_EXPANDED_BYTES
            ):
                raise RouterError(
                    "captured installed rollback archive has unsafe members"
                )
            extracted: dict[str, bytes] = {}
            for name in PACKAGE_FILES:
                member = package.extractfile(f"astrill-lazy/{name}")
                if member is None:
                    raise RouterError(
                        "captured installed rollback archive is incomplete"
                    )
                extracted[name] = member.read()
    except (EOFError, OSError, tarfile.TarError) as exc:
        raise RouterError(
            "captured installed rollback package is not a safe gzip archive"
        ) from exc

    try:
        archive_version = extracted["VERSION"].decode("ascii").strip()
    except UnicodeError as exc:
        raise RouterError(
            "captured installed rollback package VERSION is not ASCII"
        ) from exc
    stored_version = snapshot.values["astrill_lazy_version"]
    if (
        not re.fullmatch(r"[0-9A-Za-z._-]+", stored_version)
        or archive_version != stored_version
    ):
        raise RouterError("captured installed rollback package VERSION does not match")

    bootstrap_value = snapshot.values["astrill_lazy_bootstrap"]
    try:
        bootstrap = bootstrap_value.rstrip("\n")
        bootstrap_bytes = (bootstrap + "\n").encode("ascii")
    except UnicodeError as exc:
        raise RouterError("captured installed rollback bootstrap is not ASCII") from exc
    if not bootstrap.strip():
        raise RouterError("captured installed rollback bootstrap is empty")
    bootstrap_md5 = hashlib.md5(
        bootstrap_bytes,
        usedforsecurity=False,
    ).hexdigest()
    startup_lines = snapshot.values["rc_startup"].splitlines()
    digest_bound = "astrill_lazy_bootstrap_md5" in snapshot.present
    if digest_bound:
        stored_bootstrap_md5 = snapshot.values["astrill_lazy_bootstrap_md5"]
        if (
            not re.fullmatch(r"[0-9a-f]{32}", stored_bootstrap_md5)
            or stored_bootstrap_md5 != bootstrap_md5
        ):
            raise RouterError(
                "captured installed rollback bootstrap digest does not match"
            )
        launcher_compatible = (
            STARTUP_LINE in startup_lines
            and _decode_stored_bootstrap(bootstrap) is not None
        ) or (
            UNCOMPRESSED_DIGEST_STARTUP_LINE in startup_lines
            and _is_uncompressed_bootstrap(bootstrap)
        )
    else:
        launcher_compatible = _is_uncompressed_bootstrap(bootstrap) and any(
            line in startup_lines
            for line in (PREVIOUS_STARTUP_LINE, LEGACY_STARTUP_LINE)
        )
    if not launcher_compatible:
        raise RouterError(
            "captured installed rollback target has no active compatible "
            "startup launcher"
        )

    runtime_md5 = tuple(
        (
            name,
            hashlib.md5(
                extracted[name],
                usedforsecurity=False,
            ).hexdigest(),
        )
        for name in PACKAGE_FILES
    )
    return _RollbackTarget(
        bootstrap=bootstrap,
        bootstrap_md5=bootstrap_md5,
        runtime_md5=runtime_md5,
        status_has_package_md5=(
            b"""printf ',"package_md5":%s,"stored_package_md5":%s'"""
            in extracted["alctl"]
        ),
    )


def _persisted_core_boot_is_valid(values: dict[str, str]) -> bool:
    current_present = bool(
        values.get("astrill_lazy_rules_gz") or values.get("astrill_lazy_rules")
    )
    current = _restore_persisted_rule_document(
        values.get("astrill_lazy_rules_gz", ""),
        values.get("astrill_lazy_rules", ""),
    )
    if current is not None:
        return True
    previous_present = bool(
        values.get("astrill_lazy_rules_previous_gz")
        or values.get("astrill_lazy_rules_previous")
    )
    # validate-persisted-core deliberately treats boot fallback to previous
    # and quarantine-to-empty as degraded/nonzero. A rollback target must boot
    # its current record directly, not merely survive via fallback. With no
    # records at all, initialize_rules creates the canonical empty document.
    return not current_present and not previous_present


def _restore_persisted_rule_document(
    compressed_value: str,
    plain_value: str,
) -> str | None:
    if compressed_value:
        try:
            encoded = "".join(compressed_value.split())
            payload = base64.b64decode(encoded, validate=True)
            document = gzip.decompress(payload).decode("ascii")
        except (EOFError, OSError, UnicodeError, ValueError, zlib.error):
            document = ""
        if document and _validate_persisted_rule_document(document):
            return document
    if plain_value:
        # BusyBox command substitution removes stored trailing newlines before
        # restore_rule_document writes exactly one newline back to the file.
        document = plain_value.rstrip("\n") + "\n"
        if _validate_persisted_rule_document(document):
            return document
    return None


def _validate_persisted_rule_document(document: str) -> bool:
    if "\r" in document:
        return False
    try:
        payload = document.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(payload) > MAX_RULE_BYTES:
        return False
    lines = document.splitlines()
    if not lines or lines[0] != "# astrill-lazy-rules-v1":
        return False
    for line in lines[1:]:
        if line.startswith("#"):
            continue
        if not line:
            return False
        fields = line.split("\t")
        if len(fields) != 10:
            return False
        (
            rule_id,
            enabled,
            priority,
            kind,
            selector,
            target,
            protocol,
            ports,
            label,
            origin,
        ) = fields
        if not _valid_rule_id(rule_id) or not _valid_rule_id(origin):
            return False
        if enabled not in {"0", "1"}:
            return False
        if not priority.isdigit() or int(priority) > 9999:
            return False
        if kind == "domain":
            if not _valid_rule_domain(selector):
                return False
        elif kind in {"cidr", "device"}:
            if not _valid_rule_ipv4_network(selector):
                return False
        else:
            return False
        if target not in {"direct", "vpn"}:
            return False
        if protocol not in {"any", "tcp", "udp"}:
            return False
        if not _valid_rule_ports(ports):
            return False
        if ports != "-" and protocol == "any":
            return False
        if not re.fullmatch(r"[A-Za-z0-9%._~-]{1,360}", label):
            return False
    return True


def _valid_rule_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value))


def _valid_rule_domain(value: str) -> bool:
    return bool(
        len(value) <= 253
        and ".." not in value
        and re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z0-9-]+",
            value,
        )
    )


def _valid_rule_ipv4_network(value: str) -> bool:
    address, separator, prefix = value.partition("/")
    if separator and ("/" in prefix or not prefix.isdigit() or int(prefix) > 32):
        return False
    octets = address.split(".")
    return bool(
        len(octets) == 4
        and all(octet.isdigit() and 0 <= int(octet) <= 255 for octet in octets)
    )


def _valid_rule_ports(value: str) -> bool:
    if value == "-":
        return True
    entries = value.split(",")
    if not entries or len(entries) > 15:
        return False
    for entry in entries:
        bounds = entry.split(":")
        if len(bounds) > 2 or any(not bound.isdigit() for bound in bounds):
            return False
        first = int(bounds[0])
        last = int(bounds[-1])
        if not 1 <= first <= last <= 65535:
            return False
    return True


def _controller_lock_shell() -> list[str]:
    """Return the same lock protocol used by ``router/alctl``."""

    return [
        f"LOCK_DIR={CONTROLLER_LOCK_DIR}",
        'NVRAM_PRESENCE_FILE="/tmp/astrill-lazy-nvram-presence.$$"',
        "is_uint() {",
        "    case ${1:-} in",
        "        ''|*[!0-9]*) return 1 ;;",
        "        *) return 0 ;;",
        "    esac",
        "}",
        "is_live_pid() {",
        "    pid_value=${1:-}",
        '    is_uint "$pid_value" || return 1',
        '    [ "$pid_value" -gt 1 ] || return 1',
        '    kill -0 "$pid_value" 2>/dev/null',
        "}",
        "release_lock() {",
        '    rm -f "$NVRAM_PRESENCE_FILE" "/tmp/astrill-lazy-nvram-cas.$$"',
        '    rm -f "$LOCK_DIR/pid"',
        '    rmdir "$LOCK_DIR" 2>/dev/null || true',
        "    trap - EXIT INT TERM HUP",
        "}",
        "acquire_lock() {",
        "    attempts=0",
        '    while ! mkdir "$LOCK_DIR" 2>/dev/null; do',
        '        lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || printf 0)',
        '        if ! is_live_pid "$lock_pid"; then',
        "            sleep 1",
        '            lock_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || printf 0)',
        "        fi",
        '        if ! is_live_pid "$lock_pid"; then',
        '            rm -f "$LOCK_DIR/pid"',
        '            rmdir "$LOCK_DIR" 2>/dev/null || true',
        "            continue",
        "        fi",
        "        attempts=$((attempts + 1))",
        '        [ "$attempts" -lt 20 ] || return 1',
        "        sleep 1",
        "    done",
        '    printf \'%s\\n\' "$$" > "$LOCK_DIR/pid" || {',
        '        rmdir "$LOCK_DIR" 2>/dev/null || true',
        "        return 1",
        "    }",
        "    trap release_lock EXIT INT TERM HUP",
        "}",
        "nvram_hex() {",
        '    exact_file="/tmp/astrill-lazy-nvram-cas.$$"',
        '    nvram get "$1" > "$exact_file" || {',
        '        rm -f "$exact_file"',
        "        return 1",
        "    }",
        ('    actual_hex=$(hexdump -v -e \'1/1 "%02x"\' "$exact_file") || {'),
        '        rm -f "$exact_file"',
        "        return 1",
        "    }",
        '    rm -f "$exact_file"',
        "    case $actual_hex in",
        "        *0a) actual_hex=${actual_hex%0a} ;;",
        "        *) return 1 ;;",
        "    esac",
        "    printf '%s\\n' \"$actual_hex\"",
        "}",
        "refresh_nvram_presence() {",
        '    nvram show > "$NVRAM_PRESENCE_FILE" 2>/dev/null',
        "}",
        "assert_nvram() {",
        '    if grep -q "^$1=" "$NVRAM_PRESENCE_FILE"; then',
        "        actual_present=1",
        "    else",
        "        actual_present=0",
        "    fi",
        '    [ "$actual_present" = "$3" ] || return 1',
        '    [ "$3" = 1 ] || return 0',
        '    actual=$(nvram_hex "$1") || return 1',
        '    [ "$actual" = "$2" ]',
        "}",
    ]


def _nvram_free_shell() -> list[str]:
    return [
        "nvram_free_bytes() {",
        "    size_line=$(nvram show 2>&1 >/dev/null) || return 1",
        "    free_value=$(printf '%s\\n' \"$size_line\" |",
        "        sed -n 's/.*(\\([0-9][0-9]*\\) left).*/\\1/p')",
        "    case $free_value in ''|*[!0-9]*) return 1 ;; esac",
        "    printf '%s\\n' \"$free_value\"",
        "}",
    ]


def _nvram_assert_function(
    name: str,
    values: dict[str, str],
    present: frozenset[str],
) -> list[str]:
    commands = [f"{name}() {{", "    refresh_nvram_presence || return 1"]
    commands.extend(
        "    assert_nvram "
        f"{shlex.quote(key)} {shlex.quote(_nvram_value_hex(expected))} "
        f"{'1' if key in present else '0'} || return 1"
        for key, expected in sorted(values.items())
    )
    commands.extend(["    return 0", "}"])
    return commands


def _nvram_mutation_function(name: str, commands: Iterable[str]) -> list[str]:
    function = [f"{name}() {{"]
    function.extend(f"    {command} || return 1" for command in commands)
    function.extend(["    return 0", "}"])
    return function


def _nvram_restore_function(
    name: str,
    values: dict[str, str],
    present: frozenset[str],
) -> list[str]:
    function = [f"{name}() {{", "    restore_failed=0"]
    for key, value in sorted(values.items()):
        if key in present:
            command = _nvram_set_command(key, value)
        else:
            command = f"nvram unset {shlex.quote(key)}"
        function.append(f"    {command} || restore_failed=1")
    function.extend(
        [
            "    nvram commit >/dev/null || restore_failed=1",
            '    [ "$restore_failed" -eq 0 ]',
            "}",
        ]
    )
    return function
