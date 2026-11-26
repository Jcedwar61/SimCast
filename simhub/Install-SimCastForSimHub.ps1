# Installs the SimCast plugin and the SimCast Booth dashboard into SimHub.
#   Right-click > Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File Install-SimCastForSimHub.ps1
#   -SimHubDir "D:\Games\SimHub"   if SimHub isn't in its usual folder
# It copies SimCastPlugin.dll into SimHub's folder and the "SimCast Booth" folder into SimHub\DashTemplates, both
# from the folder this script is in. SimHub has to be closed (it holds its plugins open), and if SimHub is in
# Program Files, Windows asks for permission once. Run it again after a SimCast update to update both.
param([string]$SimHubDir = "")

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dll = Join-Path $here "SimCastPlugin.dll"
$dash = Join-Path $here "SimCast Booth"

function Done($msg, $code) {
    Write-Host ""
    Write-Host $msg
    if (-not $env:SIMCAST_NO_PAUSE) { Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

if (-not (Test-Path $dll)) { Done "SimCastPlugin.dll isn't next to this script. Unzip SimCast-SimHub.zip first." 1 }
if (-not (Test-Path (Join-Path $dash "SimCast Booth.djson"))) { Done "The 'SimCast Booth' folder isn't next to this script." 1 }

# --- find SimHub
$running = Get-Process -Name "SimHubWPF" -ErrorAction SilentlyContinue | Select-Object -First 1
$candidates = @()
if ($SimHubDir) { $candidates += $SimHubDir }
if ($running -and $running.Path) { $candidates += (Split-Path -Parent $running.Path) }
foreach ($root in @("HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")) {
    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($p.DisplayName -like "SimHub*" -and $p.InstallLocation) { $candidates += $p.InstallLocation }
    }
}
$candidates += @("${env:ProgramFiles(x86)}\SimHub", "$env:ProgramFiles\SimHub")
$target = $candidates | Where-Object { $_ -and (Test-Path (Join-Path $_ "SimHubWPF.exe")) } | Select-Object -First 1
if (-not $target) {
    Done "SimHub wasn't found. Install SimHub first, or run this script with -SimHubDir `"<SimHub's folder>`"." 1
}
$target = (Resolve-Path $target).Path
Write-Host "SimHub: $target"

if ($running) { Done "Close SimHub first (right-click its icon by the clock > Exit), then run this again." 1 }

# --- Program Files needs permission: ask once, in a new window
$probe = Join-Path $target ".simcast-write-test"
try { [IO.File]::WriteAllText($probe, "x"); Remove-Item $probe -Force }
catch {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) { Done "Can't write to $target ($($_.Exception.Message))." 1 }
    Write-Host "SimHub's folder needs permission to change: Windows will ask."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"",
              "-SimHubDir", "`"$target`"")
    try { Start-Process powershell -Verb RunAs -ArgumentList $argList -Wait }
    catch { Done "Permission wasn't given, so nothing was installed." 1 }
    exit 0
}

# --- copy
Copy-Item $dll (Join-Path $target "SimCastPlugin.dll") -Force
$dashDir = Join-Path $target "DashTemplates"
New-Item -ItemType Directory -Force -Path $dashDir | Out-Null
$dest = Join-Path $dashDir "SimCast Booth"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item (Join-Path $dash "*") $dest -Recurse -Force

Done ("Installed:`n  $target\SimCastPlugin.dll`n  $dest`n`n" +
      "Start SimHub: it asks once whether to enable the new 'SimCast' plugin (say yes).`n" +
      "Then Dash Studio > SimCast Booth. In SimCast, Settings > Stream > SimHub is on by default.") 0
