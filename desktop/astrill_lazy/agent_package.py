from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import Catalog
from .compiler import MAX_COMPILED_BYTES, compile_rules
from .detector import MINIMUM_BYPASS_SERVICES
from .installer import RouterInstaller, find_router_root
from .models import Compilation, MatchKind, Rule
from .store import (
    CONTROLLER_ID_RE,
    ConfigStore,
    new_controller_id,
    normalize_overlay_source,
)
from .windows_controller import MAX_OVERLAY_BYTES, MAX_OVERLAY_ROWS
from .windows_ssh_setup import WindowsHostKey

MAX_EFFECTIVE_ROWS = 640
PORTABLE_AGENT_FILE = "astrill-lazy-agent.py"
PORTABLE_INSTALL_FILE = "install-agent.sh"
PORTABLE_UNINSTALL_FILE = "uninstall-agent.sh"


@dataclass(frozen=True)
class PolicyLayerPlan:
    core_rule_ids: tuple[str, ...]
    overlay_rule_ids: tuple[str, ...]
    undeployed_rule_ids: tuple[str, ...]
    core_compilation: Compilation = field(repr=False, compare=False)
    overlay_compilation: Compilation = field(repr=False, compare=False)

    @property
    def core_bytes(self) -> int:
        return len(self.core_compilation.to_tsv().encode("ascii"))

    @property
    def overlay_bytes(self) -> int:
        return len(self.overlay_compilation.to_tsv().encode("ascii"))

    @property
    def effective_rows(self) -> int:
        return len(self.core_compilation.rules) + len(self.overlay_compilation.rules)


@dataclass(frozen=True)
class PortableAgentPackage:
    path: Path
    controller_id: str
    core_rule_ids: tuple[str, ...]
    overlay_rule_ids: tuple[str, ...]
    overlay_bytes: int
    overlay_rows: int
    overlay_md5: str
    overlay_sha256: str
    package_sha256: str


def plan_balanced_policy(
    store: ConfigStore,
    catalog: Catalog,
) -> PolicyLayerPlan:
    """Split enabled policy into a small global core and a source overlay."""

    core: list[Rule] = []
    overlay: list[Rule] = []
    undeployed: list[Rule] = []
    for rule in store.rules:
        if not rule.enabled:
            undeployed.append(rule)
            continue
        minimum_service = rule.match_kind is MatchKind.SERVICE and (
            rule.selector in MINIMUM_BYPASS_SERVICES
            or bool(rule.metadata.get("minimum_bypass"))
        )
        router_identity = rule.match_kind is MatchKind.DEVICE or (
            rule.match_kind is MatchKind.PROCESS
            and bool(str(rule.metadata.get("namespace_ip", "")).strip())
        )
        if minimum_service or router_identity:
            core.append(rule)
        elif rule.match_kind is MatchKind.PROCESS:
            undeployed.append(rule)
        else:
            overlay.append(rule)

    core_compilation = compile_rules(
        core,
        catalog,
        max_bytes=MAX_COMPILED_BYTES,
    )
    overlay_compilation = compile_rules(
        overlay,
        catalog,
        max_bytes=MAX_OVERLAY_BYTES,
    )
    if len(overlay_compilation.rules) > MAX_OVERLAY_ROWS:
        raise ValueError(
            f"balanced RAM overlay has {len(overlay_compilation.rules):,} rows, "
            f"but one controller accepts at most {MAX_OVERLAY_ROWS:,}"
        )
    device_origins = sorted(
        {
            compiled.origin
            for compiled in overlay_compilation.rules
            if compiled.kind == MatchKind.DEVICE.value
        }
    )
    if device_origins:
        raise ValueError(
            "balanced RAM overlay contains source identity rows: "
            + ", ".join(device_origins)
        )
    effective_rows = len(core_compilation.rules) + len(overlay_compilation.rules)
    if effective_rows > MAX_EFFECTIVE_ROWS:
        raise ValueError(
            f"balanced effective policy has {effective_rows:,} rows, but the "
            f"router accepts at most {MAX_EFFECTIVE_ROWS:,}"
        )
    return PolicyLayerPlan(
        core_rule_ids=tuple(rule.id for rule in core),
        overlay_rule_ids=tuple(rule.id for rule in overlay),
        undeployed_rule_ids=tuple(rule.id for rule in undeployed),
        core_compilation=core_compilation,
        overlay_compilation=overlay_compilation,
    )


def build_portable_agent_package(
    output: Path,
    *,
    store: ConfigStore,
    catalog: Catalog,
    host_key: WindowsHostKey,
    router_user: str,
    identity_file: str,
    controller_id: str | None = None,
    source: str = "auto",
    verify_interval_seconds: int = 900,
    retry_interval_seconds: int = 30,
    router_installer: RouterInstaller | None = None,
) -> PortableAgentPackage:
    plan = plan_balanced_policy(store, catalog)
    if not plan.overlay_rule_ids:
        raise ValueError("balanced policy has no RAM overlay to package")
    normalized_source = normalize_overlay_source(source)
    owner = controller_id or new_controller_id()
    if not CONTROLLER_ID_RE.fullmatch(owner):
        raise ValueError("portable agent controller ID is invalid")
    if not 300 <= verify_interval_seconds <= 3600:
        raise ValueError("agent verify interval must be between 300 and 3600 seconds")
    if not 5 <= retry_interval_seconds <= 300:
        raise ValueError("agent retry interval must be between 5 and 300 seconds")
    if not router_user.strip():
        raise ValueError("portable agent router user cannot be empty")
    if host_key.trust_state == "changed" or not host_key.fingerprint.startswith(
        "SHA256:"
    ):
        raise ValueError("portable agent requires a verified SSH host key")

    router_root = (
        router_installer.router_root
        if router_installer is not None
        else find_router_root()
    )
    installer = router_installer or RouterInstaller(object())  # type: ignore[arg-type]
    portable_agent = find_portable_agent()
    portable_install = portable_agent.with_name(PORTABLE_INSTALL_FILE)
    portable_uninstall = portable_agent.with_name(PORTABLE_UNINSTALL_FILE)
    helper = router_root / "alhybrid"
    page = router_root / "alpage-ui"
    for path in (
        portable_agent,
        portable_install,
        portable_uninstall,
        helper,
        page,
    ):
        if not path.is_file():
            raise FileNotFoundError(f"portable agent asset was not found: {path}")

    overlay = plan.overlay_compilation.to_tsv().encode("ascii")
    helper_payload = helper.read_bytes()
    page_payload = page.read_bytes()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "router_host": host_key.host,
        "router_user": router_user.strip(),
        "router_port": host_key.port,
        "identity_file": identity_file.strip(),
        "known_hosts_file": "known_hosts",
        "router_host_key_fingerprint": host_key.fingerprint,
        "companion_version": installer.expected_version,
        "companion_package_md5": installer.expected_package_md5,
        "helper_md5": hashlib.md5(
            helper_payload,
            usedforsecurity=False,
        ).hexdigest(),
        "page_md5": hashlib.md5(
            page_payload,
            usedforsecurity=False,
        ).hexdigest(),
        "controller_id": owner,
        "source": normalized_source,
        "resolved_source": None,
        "source_mac": None,
        "overlay_md5": hashlib.md5(
            overlay,
            usedforsecurity=False,
        ).hexdigest(),
        "overlay_sha256": hashlib.sha256(overlay).hexdigest(),
        "overlay_rule_ids": list(plan.overlay_rule_ids),
        "policy_bundle": _policy_bundle_provenance(store, plan.overlay_rule_ids),
        "enrolled": False,
        "overlay_generation": 0,
        "last_runtime_epoch": None,
        "last_attempt_epoch": None,
        "last_error": None,
        "verify_interval_seconds": verify_interval_seconds,
        "retry_interval_seconds": retry_interval_seconds,
    }
    output = output.expanduser().resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    assets = {
        PORTABLE_AGENT_FILE: portable_agent.read_bytes(),
        PORTABLE_INSTALL_FILE: portable_install.read_bytes(),
        PORTABLE_UNINSTALL_FILE: portable_uninstall.read_bytes(),
        "alhybrid": helper_payload,
        "alpage-ui": page_payload,
        "overlay.tsv": overlay,
        "known_hosts": (host_key.known_hosts_line + "\n").encode("ascii"),
        "manifest.json": (
            json.dumps(
                manifest,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii"),
    }
    modes = {
        PORTABLE_AGENT_FILE: 0o700,
        PORTABLE_INSTALL_FILE: 0o700,
        PORTABLE_UNINSTALL_FILE: 0o700,
        "alhybrid": 0o700,
        "alpage-ui": 0o700,
        "overlay.tsv": 0o600,
        "known_hosts": 0o600,
        "manifest.json": 0o600,
    }
    for name, payload in assets.items():
        _atomic_write(output / name, payload, modes[name])
    checksums = "".join(
        f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
        for name, payload in sorted(assets.items())
    ).encode("ascii")
    _atomic_write(output / "SHA256SUMS", checksums, 0o600)
    package_digest = hashlib.sha256(
        b"".join(name.encode("ascii") + b"\0" + assets[name] for name in sorted(assets))
    ).hexdigest()
    return PortableAgentPackage(
        path=output,
        controller_id=owner,
        core_rule_ids=plan.core_rule_ids,
        overlay_rule_ids=plan.overlay_rule_ids,
        overlay_bytes=len(overlay),
        overlay_rows=len(plan.overlay_compilation.rules),
        overlay_md5=manifest["overlay_md5"],
        overlay_sha256=manifest["overlay_sha256"],
        package_sha256=package_digest,
    )


def find_portable_agent() -> Path:
    candidates = (
        Path(__file__).resolve().parents[2]
        / "contrib"
        / "portable"
        / PORTABLE_AGENT_FILE,
        Path(sys.prefix) / "share" / "astrill-lazy" / "portable" / PORTABLE_AGENT_FILE,
        Path.home()
        / ".local"
        / "share"
        / "astrill-lazy"
        / "portable"
        / PORTABLE_AGENT_FILE,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "portable restore agent was not found in: "
        + ", ".join(str(path) for path in candidates)
    )


def _policy_bundle_provenance(
    store: ConfigStore,
    rule_ids: tuple[str, ...],
) -> dict[str, Any] | None:
    selected = set(rule_ids)
    values = {
        json.dumps(
            rule.metadata["policy_bundle"],
            ensure_ascii=True,
            sort_keys=True,
        )
        for rule in store.rules
        if rule.id in selected and isinstance(rule.metadata.get("policy_bundle"), dict)
    }
    if len(values) != 1:
        return None
    value = json.loads(next(iter(values)))
    if not isinstance(value, dict):
        return None
    policy_id = value.get("id")
    version = value.get("version")
    sha256 = value.get("sha256")
    if (
        not isinstance(policy_id, str)
        or not isinstance(version, str)
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256.casefold())
    ):
        return None
    return {
        "id": policy_id,
        "version": version,
        "sha256": sha256.casefold(),
    }


def _atomic_write(path: Path, payload: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
