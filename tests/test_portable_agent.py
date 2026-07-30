from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "contrib" / "portable" / "astrill-lazy-agent.py"


def load_agent_module() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "astrill_lazy_portable_agent",
        AGENT_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def write_agent_bundle(tmp_path: Path) -> tuple[ModuleType, dict[str, Any], Path]:
    module = load_agent_module()
    overlay = b"# astrill-lazy-rules-v1\n"
    helper = b"#!/bin/sh\nprintf helper\n"
    page = b"#!/bin/sh\nprintf page\n"
    identity = tmp_path / "router-key"
    known_hosts = tmp_path / "known_hosts"
    identity.write_text("private", encoding="ascii")
    identity.chmod(0o600)
    known_hosts.write_text(
        "192.168.1.1 ssh-ed25519 AAAATEST\n",
        encoding="ascii",
    )
    (tmp_path / "overlay.tsv").write_bytes(overlay)
    (tmp_path / "alhybrid").write_bytes(helper)
    (tmp_path / "alpage-ui").write_bytes(page)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "router_host": "192.168.1.1",
        "router_user": "root",
        "router_port": 22,
        "identity_file": str(identity),
        "known_hosts_file": str(known_hosts),
        "router_host_key_fingerprint": "SHA256:dGVzdA==",
        "companion_version": "0.2.11",
        "companion_package_md5": "a" * 32,
        "helper_md5": hashlib.md5(helper).hexdigest(),
        "page_md5": hashlib.md5(page).hexdigest(),
        "controller_id": "controller-portable-test",
        "source": "auto",
        "resolved_source": None,
        "source_mac": None,
        "overlay_md5": hashlib.md5(overlay).hexdigest(),
        "overlay_sha256": hashlib.sha256(overlay).hexdigest(),
        "overlay_rule_ids": [],
        "policy_bundle": None,
        "enrolled": False,
        "overlay_generation": 0,
        "last_runtime_epoch": None,
        "last_attempt_epoch": None,
        "last_error": None,
        "verify_interval_seconds": 900,
        "retry_interval_seconds": 30,
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest),
        encoding="ascii",
    )
    return module, manifest, manifest_path


def status_document(
    epoch: str,
    overlays: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": "0.2.11",
        "package_md5": "a" * 32,
        "stored_package_md5": "a" * 32,
        "policy_health": "ready",
        "precedence_ok": True,
        "runtime_epoch": epoch,
        "core": {
            "generation": 1,
            "hash": "md5:" + "b" * 32,
        },
        "overlays": overlays or [],
        "effective": {"rows": 1, "bytes": 20},
    }


def owner(manifest: dict[str, Any], generation: int = 1) -> dict[str, Any]:
    return {
        "owner": manifest["controller_id"],
        "generation": generation,
        "hash": "md5:" + manifest["overlay_md5"],
        "source": "192.168.1.99/32",
        "mac": "aa:bb:cc:dd:ee:ff",
    }


def test_portable_agent_stays_python_39_parseable() -> None:
    ast.parse(
        AGENT_PATH.read_text(encoding="ascii"),
        feature_version=(3, 9),
    )


def test_explicit_enrollment_records_router_source_and_mac(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, manifest, manifest_path = write_agent_bundle(tmp_path)
    agent = module.Agent(manifest_path)
    applied = owner(manifest)
    staged: list[str] = []
    monkeypatch.setattr(agent, "_verify_host_key", lambda: None)
    monkeypatch.setattr(
        agent,
        "_effective_status",
        lambda: status_document("epoch-1"),
    )
    monkeypatch.setattr(
        agent,
        "_stage_asset",
        lambda _path, target, _digest, _label: staged.append(target),
    )
    monkeypatch.setattr(
        agent,
        "_overlay_put",
        lambda **_options: status_document("epoch-1", [applied]),
    )

    result = agent.reconcile(enroll=True)
    saved = json.loads(manifest_path.read_text(encoding="ascii"))

    assert result["action"] == "enrolled"
    assert staged == [
        "/tmp/astrill-lazy/alhybrid",
        "/tmp/astrill-lazy/alpage-ui",
    ]
    assert saved["enrolled"] is True
    assert saved["resolved_source"] == "192.168.1.99/32"
    assert saved["source_mac"] == "aa:bb:cc:dd:ee:ff"
    assert saved["last_runtime_epoch"] == "epoch-1"


def test_restore_uses_the_enrolled_binding_once_per_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, manifest, manifest_path = write_agent_bundle(tmp_path)
    manifest.update(
        {
            "enrolled": True,
            "resolved_source": "192.168.1.99/32",
            "source_mac": "aa:bb:cc:dd:ee:ff",
            "last_runtime_epoch": "epoch-1",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="ascii")
    agent = module.Agent(manifest_path)
    requests: list[dict[str, str]] = []
    monkeypatch.setattr(agent, "_verify_host_key", lambda: None)
    monkeypatch.setattr(
        agent,
        "_effective_status",
        lambda: status_document("epoch-2"),
    )
    monkeypatch.setattr(agent, "_stage_asset", lambda *_args: None)

    def put(**options: str) -> dict[str, Any]:
        requests.append(options)
        return status_document("epoch-2", [owner(manifest)])

    monkeypatch.setattr(agent, "_overlay_put", put)

    assert agent.reconcile()["action"] == "restored"
    assert requests == [
        {
            "expected_source": "192.168.1.99/32",
            "expected_mac": "aa:bb:cc:dd:ee:ff",
        }
    ]
    assert agent.reconcile()["action"] == "already-attempted"
    # The fake status remains overlay-free, but the saved attempt epoch blocks
    # a second real transaction unless an operator explicitly forces it.
    assert len(requests) == 1


def test_restore_refuses_a_changed_live_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, manifest, manifest_path = write_agent_bundle(tmp_path)
    manifest.update(
        {
            "enrolled": True,
            "resolved_source": "192.168.1.99/32",
            "source_mac": "aa:bb:cc:dd:ee:ff",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="ascii")
    agent = module.Agent(manifest_path)
    changed = owner(manifest)
    changed["hash"] = "md5:" + "f" * 32
    monkeypatch.setattr(agent, "_verify_host_key", lambda: None)
    monkeypatch.setattr(
        agent,
        "_effective_status",
        lambda: status_document("epoch-drift", [changed]),
    )

    with pytest.raises(module.AgentError, match="different document"):
        agent.reconcile()
    saved = json.loads(manifest_path.read_text(encoding="ascii"))
    assert saved["last_attempt_epoch"] == "epoch-drift"
    assert "different document" in saved["last_error"]


def test_manifest_rejects_group_readable_private_key(tmp_path: Path) -> None:
    module, _manifest, manifest_path = write_agent_bundle(tmp_path)
    identity = tmp_path / "router-key"
    identity.chmod(0o640)

    with pytest.raises(module.AgentError, match="group/world"):
        module.Agent(manifest_path)
