@echo off
REM ===================================================================
REM  3olution Agent Graph - fix PHP permissions (Windows)
REM  https://3olution.com
REM
REM  Runs fix_php.ps1 with the correct execution policy, regardless of
REM  the machine's default policy. Self-elevates via UAC when needed.
REM
REM  Usage:
REM    fix_php.bat                  run all fixes
REM    fix_php.bat -SkipDefender    skip Defender exclusion
REM    fix_php.bat -SkipAcl         skip ACL reset
REM    fix_php.bat -SkipUnblock     skip Mark of the Web removal
REM    fix_php.bat -NoElevate       do not prompt for elevation
REM ===================================================================

setlocal
cd /d "%~dp0"

if not exist "fix_php.ps1" (
    echo.
    echo [x] fix_php.ps1 not found in this folder.
    echo     Both files must be in the project root.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_php.ps1" %*

set "EXITCODE=%ERRORLEVEL%"

REM 0 = PHP works. 1 = PHP still broken. 2 = needs elevation.
if "%EXITCODE%"=="0" (
    echo.
    echo PHP is ready. Restart the backend with: start.bat
    echo.
) else if "%EXITCODE%"=="2" (
    echo.
    echo Administrator privileges are required for the remaining fixes.
    echo Right-click fix_php.bat and choose "Run as administrator".
    echo.
)

endlocal & exit /b %EXITCODE%