from __future__ import annotations

import hashlib
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .router import RouterClient, RouterError

ASTRILL_INSTALL_TEMPLATE = (
    "eval `wget -q -O - http://astroutercn.com/router/install/xxx/xxx`"
)
MAX_INSTALLER_BYTES = 512 * 1024
_URL_PATTERN = re.compile(r"https?://[^\s`'\"<>]+")
_TOKEN_PATH_PATTERN = re.compile(
    r"(?P<prefix>astroutercn\.com/router/install/)[^/\s`]+/[^/\s`]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AstrillInstaller:
    script: bytes
    source: str
    sha256: str
    insecure_transport: bool

    @property
    def size(self) -> int:
        return len(self.script)


def prepare_astrill_installer(value: str) -> AstrillInstaller:
    supplied = value.strip()
    if not supplied or "/xxx/xxx" in supplied:
        raise ValueError("replace both xxx placeholders with your installer values")

    urls = _URL_PATTERN.findall(supplied)
    if supplied.startswith(("http://", "https://")) and len(urls) == 1:
        return _download_installer(urls[0])
    if len(urls) == 1 and any(command in supplied for command in ("wget", "curl")):
        return _download_installer(urls[0])

    script = supplied.encode("utf-8")
    _validate_script(script)
    return AstrillInstaller(
        script=script,
        source="Pasted shell script",
        sha256=hashlib.sha256(script).hexdigest(),
        insecure_transport=False,
    )


def install_astrill(
    router: RouterClient, installer: AstrillInstaller
) -> dict[str, Any]:
    install_error: RouterError | None = None
    try:
        router.run_script(
            installer.script.decode("utf-8") + "\n",
            timeout=180,
        )
    except RouterError as exc:
        # Vendor installers may restart SSH or reboot after writing the applet.
        install_error = exc

    last_error = ""
    for attempt in range(19):
        if attempt:
            time.sleep(5)
        try:
            status = router.native_astrill_status()
        except RouterError as exc:
            last_error = str(exc)
            continue
        if status.get("health") == "healthy":
            return status
        last_error = "the Astrill applet was not found"
    detail = last_error or (str(install_error) if install_error else "")
    raise RouterError(f"Astrill installation could not be verified: {detail}")


def redact_installer_source(value: str) -> str:
    redacted = _TOKEN_PATH_PATTERN.sub(r"\g<prefix>xxx/xxx", value)
    parts = urlsplit(redacted)
    if parts.scheme in {"http", "https"}:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    return redacted


def _download_installer(url: str) -> AstrillInstaller:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Astrill-Lazy-Router/0.2"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        script = response.read(MAX_INSTALLER_BYTES + 1)
        final_url = response.geturl()
    _validate_script(script)
    return AstrillInstaller(
        script=script,
        source=redact_installer_source(final_url),
        sha256=hashlib.sha256(script).hexdigest(),
        insecure_transport=final_url.lower().startswith("http://"),
    )


def _validate_script(script: bytes) -> None:
    if not script:
        raise ValueError("Astrill installer is empty")
    if len(script) > MAX_INSTALLER_BYTES:
        raise ValueError("Astrill installer exceeds the 512 KiB safety limit")
    if b"\x00" in script:
        raise ValueError("Astrill installer contains a NUL byte")
    try:
        script.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Astrill installer must be UTF-8 shell text") from exc
