# Setup steps the wizard (SimCast-Setup.exe) runs with PowerShell:
#   -Step close     close a SimCast running from the install folder before its files are replaced or removed
#   -Step simcoach  SimCoach (the live voice coach), if ticked: its latest setup wizard from GitHub, checked against
#                   the SHA-256 GitHub publishes for it, run silently
# Everything else the installer does needs no PowerShell (SimCast.exe --setup gets uv, Python and the packages), so
# under Wine, where there's no PowerShell, only these extras are skipped.
param([string]$Dest = $PSScriptRoot, [ValidateSet('close', 'simcoach')][string]$Step = 'close')

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$log = Join-Path $Dest 'install.log'

function Say($t) { Write-Output $t; try { Add-Content -Path $log -Value "$(Get-Date -Format s)  $t" } catch {} }

function Get-SimCastProcs {
    $script = Join-Path $Dest 'sim_cast.py'
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and $_.Name -notlike 'SimCast-Setup*' -and $_.Name -notlike 'Uninstall SimCast*' -and
        $_.Name -notlike 'Au_*' -and (
            (($_.Name -like 'python*' -or $_.Name -like 'uv*') -and $_.CommandLine -and
             $_.CommandLine.IndexOf($script, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dest + '\', [StringComparison]::OrdinalIgnoreCase) -and
             $_.Name -like 'SimCast.exe'))
    })
}

function Close-SimCast {
    $procs = Get-SimCastProcs
    if ($procs.Count -eq 0) { return $false }
    foreach ($p in $procs) {
        if ($p.Name -like 'python*') {
            try {
                $gp = Get-Process -Id $p.ProcessId -ErrorAction Stop
                if ($gp.MainWindowHandle -ne [IntPtr]::Zero) { [void]$gp.CloseMainWindow() }
            } catch {}
        }
    }
    $deadline = (Get-Date).AddSeconds(6)
    while ((Get-Date) -lt $deadline -and @(Get-SimCastProcs).Count -gt 0) { Start-Sleep -Milliseconds 300 }
    $left = @(Get-SimCastProcs | Sort-Object { if ($_.Name -like 'SimCast*') { 0 } elseif ($_.Name -like 'uv*') { 1 } else { 2 } })
    foreach ($p in $left) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    foreach ($p in $left) { Wait-Process -Id $p.ProcessId -Timeout 5 -ErrorAction SilentlyContinue }
    return $true
}

try {
    switch ($Step) {
        'close' {
            $ErrorActionPreference = 'Continue'
            if (Close-SimCast) { Say 'Closed the SimCast that was running.' }
            exit 0
        }
        'simcoach' {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $rel = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'SimCast-Setup' } `
                -Uri 'https://api.github.com/repos/Jcedwar61/SimCoach/releases/latest'
            $asset = @($rel.assets | Where-Object { $_.name -eq 'SimCoach-Setup.exe' })[0]
            $want = if ($asset) { "$($asset.digest)" -replace '^sha256:', '' } else { '' }
            if (-not $asset -or $want -notmatch '^[0-9a-fA-F]{64}$') {
                Say "SimCoach $($rel.tag_name) has no setup wizard with a checksum to install from; get it from github.com/Jcedwar61/SimCoach."
                exit 2
            }
            # a folder of its own, made fresh: nothing else can swap the file between the check and running it
            $dir = Join-Path $env:TEMP ('SimCoach-Setup-' + [guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
            $exe = Join-Path $dir 'SimCoach-Setup.exe'
            Say "Downloading SimCoach $($rel.tag_name) ..."
            Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $exe
            $got = (Get-FileHash -Algorithm SHA256 -Path $exe).Hash
            if ($got -ne $want.ToUpper()) {
                Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
                Say 'The SimCoach download did not match its published checksum, so it was not run.'
                exit 2
            }
            Say 'Installing SimCoach with its speech recognition (about 1 GB; Python and packages shared with SimCast) ...'
            $p = Start-Process -FilePath $exe -ArgumentList '/S' -Wait -PassThru
            Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
            if ($p.ExitCode -eq 0 -and (Test-Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\SimCoach')) {
                Say "SimCoach $($rel.tag_name) is installed."
                exit 0
            }
            Say "SimCoach's setup stopped (code $($p.ExitCode)); get it from github.com/Jcedwar61/SimCoach."
            exit 2
        }
    }
}
catch {
    Say "Setup step '$Step' failed: $($_.Exception.Message)"
    exit 1
}
