[CmdletBinding()]
param(
    [string]$PackagePath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if ([string]::IsNullOrWhiteSpace($PackagePath)) {
    $PackagePath = Join-Path $RepoRoot "dist\windows\Astrill Lazy Router"
}
$PackagePath = [IO.Path]::GetFullPath($PackagePath)
$PackageExecutable = Join-Path $PackagePath "Astrill Lazy Router.exe"

if (-not (Test-Path -LiteralPath $PackagePath -PathType Container)) {
    throw "The application bundle does not exist: $PackagePath"
}
if (-not (Test-Path -LiteralPath $PackageExecutable -PathType Leaf)) {
    throw "The application bundle is missing Astrill Lazy Router.exe."
}

$ProgramsRoot = [IO.Path]::GetFullPath(
    (Join-Path $env:LOCALAPPDATA "Programs")
)
$InstallDirectory = [IO.Path]::GetFullPath(
    (Join-Path $ProgramsRoot "Astrill Lazy Router")
)
$InstalledExecutable = Join-Path $InstallDirectory "Astrill Lazy Router.exe"
$ExpectedPrefix = $ProgramsRoot.TrimEnd("\") + "\"
if (-not $InstallDirectory.StartsWith(
        $ExpectedPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Refusing to install outside the per-user Programs directory."
}

function Get-InstalledProcesses {
    Get-CimInstance Win32_Process -Filter `
        "Name = 'Astrill Lazy Router.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $null -ne $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $InstalledExecutable,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
}

$Running = @(Get-InstalledProcesses)
if ($Running.Count -gt 0) {
    throw "Close Astrill Lazy Router before installing an update."
}

New-Item -ItemType Directory -Path $ProgramsRoot -Force | Out-Null

$PackageIsInstalledCopy = $PackagePath.Equals(
    $InstallDirectory,
    [StringComparison]::OrdinalIgnoreCase
)
if (-not $PackageIsInstalledCopy) {
    $Token = [Guid]::NewGuid().ToString("N")
    $StagingDirectory = Join-Path $ProgramsRoot (
        "Astrill Lazy Router.installing-$Token"
    )
    $BackupDirectory = Join-Path $ProgramsRoot (
        "Astrill Lazy Router.previous-$Token"
    )

    try {
        Copy-Item -LiteralPath $PackagePath -Destination $StagingDirectory `
            -Recurse -Force
        if (-not (Test-Path -LiteralPath (
                    Join-Path $StagingDirectory "Astrill Lazy Router.exe"
                ) -PathType Leaf)) {
            throw "The staged application bundle is incomplete."
        }

        if (Test-Path -LiteralPath $InstallDirectory) {
            Move-Item -LiteralPath $InstallDirectory `
                -Destination $BackupDirectory
        }
        try {
            Move-Item -LiteralPath $StagingDirectory `
                -Destination $InstallDirectory
        } catch {
            if ((Test-Path -LiteralPath $BackupDirectory) -and
                -not (Test-Path -LiteralPath $InstallDirectory)) {
                Move-Item -LiteralPath $BackupDirectory `
                    -Destination $InstallDirectory
            }
            throw
        }
        if (Test-Path -LiteralPath $BackupDirectory) {
            Remove-Item -LiteralPath $BackupDirectory -Recurse -Force
        }
    } finally {
        if (Test-Path -LiteralPath $StagingDirectory) {
            Remove-Item -LiteralPath $StagingDirectory -Recurse -Force
        }
    }
}

if (-not (Test-Path -LiteralPath $InstalledExecutable -PathType Leaf)) {
    throw "Installation did not produce $InstalledExecutable."
}

$DesktopShortcut = Join-Path (
    [Environment]::GetFolderPath("Desktop")
) "Astrill Lazy Router.lnk"
$StartupDirectory = [Environment]::GetFolderPath("Startup")
$StartupShortcut = Join-Path $StartupDirectory "Astrill Lazy Router.lnk"
$StartMenuShortcut = Join-Path (
    [Environment]::GetFolderPath("Programs")
) "Astrill Lazy Router.lnk"

New-Item -ItemType Directory -Path $StartupDirectory -Force | Out-Null

$Shell = New-Object -ComObject WScript.Shell
try {
    $Shortcut = $Shell.CreateShortcut($DesktopShortcut)
    $Shortcut.TargetPath = $InstalledExecutable
    $Shortcut.Arguments = ""
    $Shortcut.WorkingDirectory = $InstallDirectory
    $Shortcut.IconLocation = "$InstalledExecutable,0"
    $Shortcut.Description = "Control Astrill policy routing"
    $Shortcut.Save()

    $Shortcut = $Shell.CreateShortcut($StartupShortcut)
    $Shortcut.TargetPath = $InstalledExecutable
    $Shortcut.Arguments = ""
    $Shortcut.WorkingDirectory = $InstallDirectory
    $Shortcut.IconLocation = "$InstalledExecutable,0"
    $Shortcut.Description = "Launch Astrill Lazy Router after sign-in"
    $Shortcut.Save()
} finally {
    if ($null -ne $Shell) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($Shell)
    }
}

# Native installs intentionally have no Start Menu entry. Remove the older
# same-named launcher if it is present.
Remove-Item -LiteralPath $StartMenuShortcut -Force `
    -ErrorAction SilentlyContinue

Write-Output "Installed Astrill Lazy Router at $InstallDirectory"
Write-Output "Created Desktop shortcut: $DesktopShortcut"
Write-Output "Enabled login startup: $StartupShortcut"
Write-Output "The application was not launched."
