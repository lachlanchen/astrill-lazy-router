[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$VirtualEnvironment,
    [string]$DistPath,
    [string]$WorkPath,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$SpecPath = Join-Path $PSScriptRoot "AstrillLazyRouter.spec"

if ([string]::IsNullOrWhiteSpace($VirtualEnvironment)) {
    $VirtualEnvironment = Join-Path $RepoRoot "build\windows-venv"
}
if ([string]::IsNullOrWhiteSpace($DistPath)) {
    $DistPath = Join-Path $RepoRoot "dist\windows"
}
if ([string]::IsNullOrWhiteSpace($WorkPath)) {
    $WorkPath = Join-Path $RepoRoot "build\pyinstaller-windows"
}

$VirtualEnvironment = [IO.Path]::GetFullPath($VirtualEnvironment)
$DistPath = [IO.Path]::GetFullPath($DistPath)
$WorkPath = [IO.Path]::GetFullPath($WorkPath)

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath exited with code $LASTEXITCODE."
    }
}

if (Test-Path -LiteralPath $Python -PathType Leaf) {
    $BootstrapPython = [IO.Path]::GetFullPath($Python)
} else {
    $PythonCommand = Get-Command $Python -CommandType Application `
        -ErrorAction Stop | Select-Object -First 1
    $BootstrapPython = $PythonCommand.Source
}

$VenvPython = Join-Path $VirtualEnvironment "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    $VenvParent = Split-Path -Parent $VirtualEnvironment
    New-Item -ItemType Directory -Path $VenvParent -Force | Out-Null
    Write-Output "Creating the Windows build environment at $VirtualEnvironment"
    Invoke-Checked -FilePath $BootstrapPython -Arguments @(
        "-m", "venv", $VirtualEnvironment
    )
}

if (-not $SkipDependencyInstall) {
    Write-Output "Installing the Windows build dependencies."
    Invoke-Checked -FilePath $VenvPython -Arguments @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--upgrade",
        "pip",
        "setuptools",
        "wheel"
    )
    Invoke-Checked -FilePath $VenvPython -Arguments @(
        "-m", "pip", "install",
        "--disable-pip-version-check",
        "--upgrade",
        "-e", "${RepoRoot}[windows]"
    )
}

Invoke-Checked -FilePath $VenvPython -Arguments @(
    "-c",
    "import PyInstaller, PySide6; print(f'PyInstaller {PyInstaller.__version__}; PySide6 {PySide6.__version__}')"
)

New-Item -ItemType Directory -Path $DistPath -Force | Out-Null
New-Item -ItemType Directory -Path $WorkPath -Force | Out-Null

Write-Output "Building the native Windows application."
Push-Location $RepoRoot
try {
    Invoke-Checked -FilePath $VenvPython -Arguments @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", $DistPath,
        "--workpath", $WorkPath,
        $SpecPath
    )
} finally {
    Pop-Location
}

$BundlePath = Join-Path $DistPath "Astrill Lazy Router"
$ExecutablePath = Join-Path $BundlePath "Astrill Lazy Router.exe"
if (-not (Test-Path -LiteralPath $ExecutablePath -PathType Leaf)) {
    throw "PyInstaller completed without producing $ExecutablePath."
}

$RuntimeRoots = @(
    $BundlePath,
    (Join-Path $BundlePath "_internal")
)
foreach ($RelativePath in @(
    "extensions\core-catalog\manifest.json",
    "router\VERSION",
    "schemas\device-policy-v1.schema.json"
)) {
    $Found = $false
    foreach ($RuntimeRoot in $RuntimeRoots) {
        if (Test-Path -LiteralPath (Join-Path $RuntimeRoot $RelativePath) `
                -PathType Leaf) {
            $Found = $true
            break
        }
    }
    if (-not $Found) {
        throw "The built application is missing bundled data: $RelativePath"
    }
}

Write-Output "Built native Windows application: $ExecutablePath"
Write-Output (
    "The build was not launched. Run install-native.ps1 to install it."
)
