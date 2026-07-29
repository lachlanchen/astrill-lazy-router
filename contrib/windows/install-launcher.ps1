[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Source = Join-Path $PSScriptRoot "Open-AstrillLazyRouter.ps1"
$InstallDirectory = Join-Path $env:LOCALAPPDATA "AstrillLazyRouter"
$InstalledScript = Join-Path $InstallDirectory "Open-AstrillLazyRouter.ps1"
$DesktopShortcut = Join-Path (
    [Environment]::GetFolderPath("Desktop")
) "Astrill Lazy Router.lnk"
$StartMenuShortcut = Join-Path (
    [Environment]::GetFolderPath("Programs")
) "Astrill Lazy Router.lnk"

New-Item -ItemType Directory -Path $InstallDirectory -Force | Out-Null
Copy-Item -LiteralPath $Source -Destination $InstalledScript -Force

$Shell = New-Object -ComObject WScript.Shell
foreach ($ShortcutPath in @($DesktopShortcut, $StartMenuShortcut)) {
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $Shortcut.Arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$InstalledScript`""
    $Shortcut.WorkingDirectory = $InstallDirectory
    $Shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
    $Shortcut.Description = "Open Astrill Lazy Router through a secure SSH tunnel"
    $Shortcut.Save()
}

Write-Output "Installed Desktop and Start Menu shortcuts for Astrill Lazy Router."
