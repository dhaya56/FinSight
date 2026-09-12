@echo off
setlocal

rem FinSight environment status report.
rem
rem Read-only: starts no service, installs nothing, downloads nothing.
rem Output is sanitized for the ENV-001 record: no user names, absolute paths,
rem IP addresses, proxy settings, or internal network details are printed.
rem
rem Run from the repository root with the project virtual environment activated.

echo ==============================================
echo FinSight environment status
echo Date: %DATE%
echo ==============================================
echo.

echo [Host]
powershell -NoProfile -Command "$o=Get-CimInstance Win32_OperatingSystem; 'OS: ' + $o.Caption + ' build ' + $o.BuildNumber" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Processor | ForEach-Object { 'CPU: ' + $_.Name.Trim(); 'Physical cores: ' + $_.NumberOfCores; 'Logical processors: ' + $_.NumberOfLogicalProcessors }" 2>nul
powershell -NoProfile -Command "'Total physical memory (GB): ' + [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)" 2>nul
powershell -NoProfile -Command "$d=(Get-Location).Drive; 'Project volume: ' + $d.Name + ' | Free space (GB): ' + [math]::Round($d.Free/1GB,1)" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_VideoController | ForEach-Object { 'Graphics adapter: ' + $_.Name }" 2>nul
echo.

echo [Python]
python --version 2>nul || echo Python: not available on PATH
python -c "import sys; print('Virtual environment active:', sys.prefix != sys.base_prefix)" 2>nul
python -c "import pip; print('pip:', pip.__version__)" 2>nul || echo pip: not available
echo.

echo [Docker]
where docker >nul 2>&1
if errorlevel 1 (
    echo Docker: not available on PATH
) else (
    docker --version
    docker info --format "Container backend OS type: {{.OSType}}" 2>nul || echo Docker daemon: not responding
    docker compose version 2>nul || echo Docker Compose: not available
)
echo.

echo [WSL]
where wsl >nul 2>&1
if errorlevel 1 (
    echo WSL: not available on PATH
) else (
    wsl --version 2>nul || echo WSL version: not reported
    wsl --list --verbose 2>nul || echo WSL distributions: not reported
)
echo.

echo [Ollama]
where ollama >nul 2>&1
if errorlevel 1 (
    echo Ollama: not available on PATH
) else (
    ollama --version 2>nul
)
powershell -NoProfile -Command "try { $r=(Invoke-WebRequest -Uri http://localhost:11434/api/version -UseBasicParsing -TimeoutSec 3).Content; 'Local Ollama endpoint: reachable ' + $r } catch { 'Local Ollama endpoint: not reachable' }" 2>nul
echo.

echo ==============================================
echo Status report complete. Nothing was started or installed.
echo ==============================================

endlocal
