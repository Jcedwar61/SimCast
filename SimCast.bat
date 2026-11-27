@echo off
setlocal
title SimCast
cd /d "%~dp0"
rem SimCast: live TV-style race commentary. Uses uv (fast Python runner); installs what it needs on first run.
where uv >nul 2>nul
if %errorlevel%==0 goto run
if exist "%USERPROFILE%\.local\bin\uv.exe" (set "PATH=%USERPROFILE%\.local\bin;%PATH%" & goto run)
if exist "%USERPROFILE%\.cargo\bin\uv.exe" (set "PATH=%USERPROFILE%\.cargo\bin;%PATH%" & goto run)
echo First run: downloading uv 0.13.10 (Python runner, from pypi.org, checksum-checked)...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; $u='https://files.pythonhosted.org/packages/cf/e4/d0704c116ada58e8450890e2defaaf2f07e8d264cf94669210e0823fbcb2/uv-0.13.10-py3-none-win_amd64.whl'; $h='d87b9cf8165f2b3cc596e856b5bf794da9cfad6d1b110ca9d87eed42a15b3684'; if($env:PROCESSOR_ARCHITECTURE -eq 'ARM64'){$u='https://files.pythonhosted.org/packages/8c/d7/59a108985cfc5f1cb0afea1108ff5a0bc4994b405af0825fb3085f068de8/uv-0.13.10-py3-none-win_arm64.whl'; $h='8b0a15613aae9a3e1c134bb5ee706583af0addced7eb1add992f8b1b3e3a92a0'}; $d=Join-Path $env:USERPROFILE '.local\bin'; $z=Join-Path $env:TEMP ('simcast-uv-'+[guid]::NewGuid()+'.zip'); $x=$z+'.d'; Invoke-WebRequest -UseBasicParsing $u -OutFile $z; if((Get-FileHash -Algorithm SHA256 $z).Hash -ne $h){Remove-Item $z; throw 'uv download does not match its checksum'}; Expand-Archive -Force $z $x; New-Item -ItemType Directory -Force $d | Out-Null; Copy-Item (Join-Path $x 'uv-0.13.10.data\scripts\*.exe') $d -Force; Remove-Item -Recurse -Force $z,$x"
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
:run
echo Starting SimCast... (the first launch downloads its parts, a minute or two)
uv run --python 3.12 sim_cast.py %*
echo.
pause
