from pathlib import Path


repo_root = Path(SPECPATH).resolve().parents[1]
entrypoint = repo_root / "contrib" / "windows" / "native-entry.py"
desktop_root = repo_root / "desktop"

datas = [
    (
        str(repo_root / "extensions"),
        "extensions",
    ),
    (
        str(repo_root / "router"),
        "router",
    ),
    (
        str(repo_root / "schemas"),
        "schemas",
    ),
    (
        str(repo_root / "LICENSE"),
        "licenses",
    ),
]

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(desktop_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["gi"],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Astrill Lazy Router",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Astrill Lazy Router",
)
