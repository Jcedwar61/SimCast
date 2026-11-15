# NSIS with 64-bit stubs, for building SimCast-Setup.exe on Windows.
#
# The NSIS setup and the winget package are x86-only: they have no amd64-unicode stubs or plugins, so the build
# stops at simcast.nsi's "Target amd64-unicode". Debian ships the 64-bit stubs and plugins, and they are plain
# files, so this fetches the official NSIS 3.13 build, adds them, and prints the folder to put on PATH:
#
#   $env:PATH = "$(tools/installer/nsis64.ps1)" + ";C:\Program Files\7-Zip;" + $env:PATH
#   bash tools/installer/build_installer.sh
#
# On Linux, apt's nsis package already has them. Needs internet; somewhere to cache under %LOCALAPPDATA%.
param([string]$Dir = (Join-Path $env:LOCALAPPDATA "SimCast-build\nsis"))

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$version = '3.13'
$exe = Join-Path $Dir 'makensis.exe'

function Get-File($url, $to) {
    curl.exe -sS -L --max-time 180 -o $to $url
    if ($LASTEXITCODE -ne 0 -or (Get-Item $to).Length -lt 100000) { throw "Download failed: $url" }
}

function Expand($archive, $to) {
    $sevenZip = @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $sevenZip) { throw "7-Zip is needed to unpack the archives (https://7-zip.org)" }
    New-Item -ItemType Directory -Force $to | Out-Null
    & $sevenZip x -y "-o$to" $archive | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not unpack $archive" }
}

if ((Test-Path $exe) -and (Test-Path (Join-Path $Dir 'Stubs\zlib-amd64-unicode'))) { $Dir; exit 0 }

New-Item -ItemType Directory -Force $Dir | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("nsis64-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Force $tmp | Out-Null
try {
    # The official NSIS build: the same version Debian packages, so its stubs and plugins match its makensis.
    $zip = Join-Path $tmp 'nsis.zip'
    foreach ($mirror in @("master.dl.sourceforge.net", "deac-riga.dl.sourceforge.net", "netix.dl.sourceforge.net")) {
        try { Get-File "https://$mirror/project/nsis/NSIS%203/$version/nsis-$version.zip?viasf=1" $zip; break }
        catch { if ($mirror -eq "netix.dl.sourceforge.net") { throw } }
    }
    Expand $zip (Join-Path $tmp 'nsis')
    $root = (Get-ChildItem (Join-Path $tmp 'nsis') -Directory | Select-Object -First 1).FullName
    Copy-Item (Join-Path $root '*') $Dir -Recurse -Force

    # The same version's amd64 stubs and plugins, out of Debian's nsis-common package.
    $deb = Join-Path $tmp 'nsis-common.deb'
    Get-File "https://deb.debian.org/debian/pool/main/n/nsis/nsis-common_$version-1_all.deb" $deb
    Expand $deb (Join-Path $tmp 'deb')
    $data = Get-ChildItem (Join-Path $tmp 'deb') -Filter 'data.tar*' | Select-Object -First 1
    if ($data.Extension -ne '.tar') { Expand $data.FullName (Join-Path $tmp 'xz'); $data = Get-Item (Join-Path $tmp 'xz\*') }
    Expand $data.FullName (Join-Path $tmp 'common')
    $share = Join-Path $tmp 'common\usr\share\nsis'
    Copy-Item (Join-Path $share 'Stubs\*-amd64-unicode') (Join-Path $Dir 'Stubs') -Force
    New-Item -ItemType Directory -Force (Join-Path $Dir 'Plugins\amd64-unicode') | Out-Null
    Copy-Item (Join-Path $share 'Plugins\amd64-unicode\*') (Join-Path $Dir 'Plugins\amd64-unicode') -Force
    Copy-Item (Join-Path $share 'Include\*') (Join-Path $Dir 'Include') -Recurse -Force
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path (Join-Path $Dir 'Stubs\zlib-amd64-unicode'))) { throw "The 64-bit stubs are still missing in $Dir" }
$Dir
