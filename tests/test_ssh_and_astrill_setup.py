from __future__ import annotations

import os
import re
from pathlib import Path

from astrill_lazy.astrill_install import (
    ASTRILL_INSTALL_TEMPLATE,
    install_astrill,
    prepare_astrill_installer,
    redact_installer_source,
)
from astrill_lazy.ssh_setup import ensure_local_identity, read_public_key

ROOT = Path(__file__).parents[1]


def test_local_router_identity_is_generated_with_private_permissions(
    tmp_path: Path,
) -> None:
    identity = tmp_path / ".ssh" / "router"

    assert ensure_local_identity(str(identity)) == identity
    if os.name != "nt":
        assert identity.stat().st_mode & 0o777 == 0o600
        assert identity.with_name("router.pub").stat().st_mode & 0o777 == 0o644
    assert read_public_key(str(identity)).startswith("ssh-ed25519 ")


def test_custom_identity_does_not_change_an_existing_parent_mode(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)

    ensure_local_identity(str(parent / "router"))

    if os.name != "nt":
        assert parent.stat().st_mode & 0o777 == 0o755


def test_astrill_installer_template_and_source_redaction_never_embed_a_token() -> None:
    assert "/xxx/xxx" in ASTRILL_INSTALL_TEMPLATE
    example_segments = ("example-user", "example-token")
    source = (
        "http://" + "astroutercn" + ".com/router/install/" + "/".join(example_segments)
    )
    assert redact_installer_source(f"{source}?token=private").endswith(
        "/install/xxx/xxx"
    )

    token_path = re.compile(
        r"astroutercn\.com/router/install/(?!xxx(?:/|$))[^/\s`]+/"
        r"(?!xxx(?:\s|`|$))[^/\s`]+",
        re.IGNORECASE,
    )
    for path in ROOT.rglob("*"):
        if (
            ".git" in path.parts
            or ".venv" in path.parts
            or ".pytest_cache" in path.parts
            or ".private-backups" in path.parts
            or "build" in path.parts
            or "dist" in path.parts
            or path.suffix.lower() in {".png", ".gz", ".cms", ".pyc"}
        ):
            continue
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        assert token_path.search(text) is None, path.relative_to(ROOT)


def test_pasted_astrill_script_is_transient_and_hashed() -> None:
    installer = prepare_astrill_installer("#!/bin/sh\nprintf ready\n")

    assert installer.source == "Pasted shell script"
    assert installer.script.startswith(b"#!/bin/sh")
    assert len(installer.sha256) == 64
    assert not installer.insecure_transport


def test_astrill_install_verifies_the_native_applet() -> None:
    class HealthyRouter:
        def __init__(self) -> None:
            self.script = ""

        def run_script(self, script: str, *, timeout: int) -> str:
            self.script = script
            assert timeout == 180
            return ""

        def native_astrill_status(self) -> dict[str, str]:
            return {"health": "healthy"}

    router = HealthyRouter()
    installer = prepare_astrill_installer("#!/bin/sh\nprintf installed\n")

    assert install_astrill(router, installer) == {"health": "healthy"}  # type: ignore[arg-type]
    assert "printf installed" in router.script
