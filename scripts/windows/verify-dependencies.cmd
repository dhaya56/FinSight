@echo off
setlocal

rem FinSight dependency verification.
rem
rem Read-only: installs, upgrades, and removes nothing. Findings are printed
rem verbatim for review.
rem
rem Run from the repository root with the project virtual environment activated.

set FINSIGHT_VERIFY_FAILED=0

echo ==============================================
echo FinSight dependency verification
echo ==============================================
echo.

echo [Dependency consistency: pip check]
python -m pip check
if errorlevel 1 set FINSIGHT_VERIFY_FAILED=1
echo.

echo [Known vulnerabilities: pip-audit]
echo Findings below are review items, not automatic failures.
python -m pip_audit
echo.

echo [Import check]
python -c "import pydantic, pydantic_settings; print('runtime imports ok')"
if errorlevel 1 set FINSIGHT_VERIFY_FAILED=1
python -c "import sys; sys.path.insert(0, 'src'); import finsight; print('finsight', finsight.__version__)"
if errorlevel 1 set FINSIGHT_VERIFY_FAILED=1
echo.

if "%FINSIGHT_VERIFY_FAILED%"=="1" (
    echo ==============================================
    echo RESULT: dependency consistency or import check FAILED
    echo ==============================================
    endlocal
    exit /b 1
)

echo ==============================================
echo RESULT: dependency consistency and imports OK
echo Review any pip-audit findings printed above.
echo ==============================================

endlocal
exit /b 0
