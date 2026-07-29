[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
    throw "Refusing to remove a directory outside per-user Programs."
}

$Running = @(
    Get-CimInstance Win32_Process -Filter `
        "Name = 'Astrill Lazy Router.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $null -ne $_.ExecutablePath -and
            [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $InstalledExecutable,
                [StringComparison]::OrdinalIgnoreCase
            )
        }
)
if ($Running.Count -gt 0 -and -not $Force) {
    throw (
        "Astrill Lazy Router is running. Close it first or rerun with -Force."
    )
}
if ($Running.Count -gt 0) {
    foreach ($Process in $Running) {
        Stop-Process -Id $Process.ProcessId -Force
    }
    foreach ($Process in $Running) {
        Wait-Process -Id $Process.ProcessId -Timeout 10 `
            -ErrorAction SilentlyContinue
    }
}

function Remove-MatchingShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ShortcutPath
    )

    if (-not (Test-Path -LiteralPath $ShortcutPath -PathType Leaf)) {
        return
    }
    $Shell = New-Object -ComObject WScript.Shell
    try {
        $Shortcut = $Shell.CreateShortcut($ShortcutPath)
        $Target = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($Shortcut.TargetPath)
        )
        if ($Target.Equals(
                $InstalledExecutable,
                [StringComparison]::OrdinalIgnoreCase
            )) {
            Remove-Item -LiteralPath $ShortcutPath -Force
        }
    } finally {
        if ($null -ne $Shell) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                $Shell
            )
        }
    }
}

$DesktopShortcut = Join-Path (
    [Environment]::GetFolderPath("Desktop")
) "Astrill Lazy Router.lnk"
$StartMenuShortcut = Join-Path (
    [Environment]::GetFolderPath("Programs")
) "Astrill Lazy Router.lnk"
Remove-MatchingShortcut -ShortcutPath $DesktopShortcut
Remove-MatchingShortcut -ShortcutPath $StartMenuShortcut

if (Test-Path -LiteralPath $InstallDirectory) {
    Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
    Write-Output "Removed Astrill Lazy Router from $InstallDirectory"
} else {
    Write-Output "Astrill Lazy Router is already uninstalled."
}

Write-Output (
    "User configuration and SSH keys were preserved."
)
