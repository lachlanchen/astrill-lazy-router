from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parents[1]
LOCALIZED_READMES = (
    ROOT / "README.md",
    ROOT / "i18n" / "README.ar.md",
    ROOT / "i18n" / "README.es.md",
    ROOT / "i18n" / "README.fr.md",
    ROOT / "i18n" / "README.ja.md",
    ROOT / "i18n" / "README.ko.md",
    ROOT / "i18n" / "README.vi.md",
    ROOT / "i18n" / "README.zh-Hans.md",
    ROOT / "i18n" / "README.zh-Hant.md",
    ROOT / "i18n" / "README.de.md",
    ROOT / "i18n" / "README.ru.md",
)
LANGUAGE_LABELS = (
    "English",
    "العربية",
    "Español",
    "Français",
    "日本語",
    "한국어",
    "Tiếng Việt",
    "中文 (简体)",
    "中文（繁體）",
    "Deutsch",
    "Русский",
)


def test_all_localized_readmes_have_complete_navigation_and_branding() -> None:
    assert len(LOCALIZED_READMES) == 11
    for path in LOCALIZED_READMES:
        text = path.read_text(encoding="utf-8")
        header = text[:1800]
        assert "# Astrill Lazy Router" in header
        assert "LazyingArt banner" in header
        assert "https://lazying.art" in header
        assert "Direct" in text
        assert "Astrill" in text
        assert "https://chat.lazying.art/donate" in text
        assert "https://paypal.me/RongzhouChen" in text
        assert "https://buy.stripe.com/" in text
        for label in LANGUAGE_LABELS:
            assert f"[{label}]" in header


def test_readme_relative_links_resolve() -> None:
    paths = (*LOCALIZED_READMES, ROOT / "i18n" / "README.md")
    missing: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for raw_target in re.findall(r"\]\(([^)]+)\)", text):
            target = raw_target.strip().split(maxsplit=1)[0]
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative = unquote(target.split("#", 1)[0])
            if relative and not (path.parent / relative).resolve().exists():
                missing.append(f"{path.relative_to(ROOT)} -> {relative}")
    assert not missing, "\n".join(missing)


def test_readme_assets_and_funding_configuration_exist() -> None:
    assert (ROOT / "figs" / "banner.png").stat().st_size > 100_000
    assert (
        ROOT / "docs" / "assets" / "services-country-batch.png"
    ).stat().st_size > 10_000
    funding = (ROOT / ".github" / "FUNDING.yml").read_text(encoding="utf-8")
    assert "github: [lachlanchen]" in funding
    assert "https://chat.lazying.art/donate" in funding
