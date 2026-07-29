[CmdletBinding()]
param(
    [int]$LocalPort = 16087,
    [int]$RemotePort = 6087,
    [string[]]$SshHosts = @(
        "lachlan@OptiPlex-7090.local",
        "lachlan@192.168.1.100",
        "lachlan@lachlanserver.local",
        "lachlan@lachlanserver",
        "lachlan@192.168.24.108"
    )
)

$ErrorActionPreference = "Stop"
$StateDirectory = Join-Path $env:LOCALAPPDATA "AstrillLazyRouter"
$PidFile = Join-Path $StateDirectory "tunnel.pid"
$LogFile = Join-Path $StateDirectory "tunnel.log"
$OutLogFile = Join-Path $StateDirectory "tunnel.out.log"
$HealthUrl = "http://127.0.0.1:$LocalPort/vnc.html"
$ControllerUrl = "${HealthUrl}?host=127.0.0.1&port=$LocalPort&autoconnect=1&resize=scale"
$PortSignature = "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}"

function Test-Controller {
    & curl.exe --silent --fail --max-time 1 --output NUL $HealthUrl
    return $LASTEXITCODE -eq 0
}

function Stop-OwnedTunnel {
    if (Test-Path -LiteralPath $PidFile) {
        $SavedPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
        if ($SavedPid -match "^[0-9]+$") {
            $Process = Get-CimInstance Win32_Process `
                -Filter "ProcessId = $SavedPid" -ErrorAction SilentlyContinue
            if ($null -ne $Process -and
                $Process.Name -ieq "ssh.exe" -and
                $Process.CommandLine.Contains($PortSignature)) {
                Stop-Process -Id ([int]$SavedPid) -Force `
                    -ErrorAction SilentlyContinue
            }
        }
    }
    Get-CimInstance Win32_Process -Filter "Name = 'ssh.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object {
            $null -ne $_.CommandLine -and
            $_.CommandLine.Contains($PortSignature)
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
if (Test-Controller) {
    Start-Process $ControllerUrl
    exit 0
}

Stop-OwnedTunnel
$LastError = "No SSH host was reachable."
foreach ($SshHost in $SshHosts) {
    $Arguments = @(
        "-N",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=4",
        "-o", "ConnectionAttempts=1",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-L", $PortSignature,
        $SshHost
    )
    $Tunnel = Start-Process -FilePath "ssh.exe" -ArgumentList $Arguments `
        -WindowStyle Hidden -RedirectStandardOutput $OutLogFile `
        -RedirectStandardError $LogFile -PassThru

    foreach ($Attempt in 1..16) {
        if (Test-Controller) {
            Set-Content -LiteralPath $PidFile -Value $Tunnel.Id -Encoding ascii
            Start-Process $ControllerUrl
            Wait-Process -Id $Tunnel.Id -ErrorAction SilentlyContinue
            exit 0
        }
        if ($Tunnel.HasExited) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
    $LastError = if (Test-Path -LiteralPath $LogFile) {
        (Get-Content -LiteralPath $LogFile -Tail 4) -join [Environment]::NewLine
    } else {
        "SSH tunnel to $SshHost did not become ready."
    }
    Stop-OwnedTunnel
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show(
    "Could not reach the secure Astrill Lazy Router controller.`n`n$LastError",
    "Astrill Lazy Router",
    "OK",
    "Error"
) | Out-Null
exit 1
