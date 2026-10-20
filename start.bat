@echo off
REM ======================================================================
REM  3olution Agent Graph - start the backend (Windows)
REM  https://3olution.com
REM
REM  Launches the FastAPI backend using the portable Python interpreter
REM  in runtime\. If the runtime is missing, this script runs setup.ps1
REM  automatically, then starts the backend.
REM
REM  Usage:
REM    start.bat                    start the backend on 127.0.0.1:8000
REM    start.bat --port 9000        custom port
REM    start.bat --reload           enable uvicorn auto-reload
REM
REM  Environment:
REM    AGENT_HOST                   default bind host (127.0.0.1)
REM    AGENT_PORT                   default bind port (8000)
REM
REM  Stopping:
REM    Press Ctrl+C in this window. The MCP subprocess, llama-cpp
REM    server (if any), and PHP-CGI children are cleaned up by the
REM    backend's lifespan hook.
REM ======================================================================

setlocal EnableDelayedExpansion
pushd "%~dp0"

REM --------------------------------------------------------------------
REM  Locate the portable Python
REM --------------------------------------------------------------------

set "PYTHON_BIN=runtime\python\python.exe"
set "SERVE_SCRIPT=scripts\serve.py"

REM --------------------------------------------------------------------
REM  Parse arguments
REM --------------------------------------------------------------------

set "HOST=127.0.0.1"
set "PORT=8000"
set "RELOAD=0"

if defined AGENT_HOST set "HOST=%AGENT_HOST%"
if defined AGENT_PORT set "PORT=%AGENT_PORT%"

:parse_args
if "%~1"=="" goto args_done
if /i "%~1"=="--host" (
    set "HOST=%~2"
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--port" (
    set "PORT=%~2"
    shift
    shift
    goto parse_args
)
if /i "%~1"=="--reload" (
    set "RELOAD=1"
    shift
    goto parse_args
)
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h"     goto show_help
REM Unknown flag - ignore for now (reserved for future).
shift
goto parse_args

:show_help
echo.
echo 3olution Agent Graph - start the backend
echo.
echo Usage: start.bat [OPTIONS]
echo.
echo Options:
echo   --host HOST      bind address (default: %HOST%)
echo   --port PORT      bind port    (default: %PORT%)
echo   --reload         enable uvicorn auto-reload
echo   --help, -h       show this message
echo.
echo Environment:
echo   AGENT_HOST       default bind address
echo   AGENT_PORT       default bind port
echo.
echo The backend will be available at:
echo   http://%HOST%:%PORT%/             Chat UI
echo   http://%HOST%:%PORT%/graph        Graph editor
echo   http://%HOST%:%PORT%/settings     Settings
echo.
popd
endlocal
exit /b 0

:args_done

REM --------------------------------------------------------------------
REM  Print banner
REM --------------------------------------------------------------------

echo.
echo ==================================================================
echo   3olution Agent Graph - Starting Backend
echo   https://3olution.com
echo ==================================================================
echo.

REM --------------------------------------------------------------------
REM  Verify the runtime is installed, run setup if not
REM --------------------------------------------------------------------

if not exist "%PYTHON_BIN%" (
    echo [warn] Portable runtime not found at %PYTHON_BIN%
    echo [info] Running setup.ps1 first...
    echo.

    REM Invoke setup.ps1 with a bypass of the execution policy so we
    REM don't require the user to change their policy permanently.
    REM -NoProfile keeps the run fast and avoids user-profile quirks.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    if errorlevel 1 (
        echo.
        echo [error] Setup failed. Fix the errors above and re-run.
        echo.
        echo         As a fallback, try running setup via Python directly:
        echo             python run.py setup
        echo         Or:
        echo             python bootstrap\setup.py
        echo.
        popd
        endlocal
        exit /b 1
    )

    echo.
    if not exist "%PYTHON_BIN%" (
        echo [error] Setup completed but %PYTHON_BIN% is still missing.
        echo         Try: runtime\python\python.exe bootstrap\setup.py --force --verbose
        popd
        endlocal
        exit /b 1
    )
)

if not exist "%SERVE_SCRIPT%" (
    echo [error] Server launcher not found: %SERVE_SCRIPT%
    echo         The project appears to be incomplete.
    popd
    endlocal
    exit /b 1
)

REM --------------------------------------------------------------------
REM  Verify .env (or warn)
REM --------------------------------------------------------------------

if not exist ".env" (
    echo [warn] No .env file found. Using built-in defaults:
    echo          LLM:    http://localhost:11434/v1  ^(Ollama^)
    echo          Model:  deepseek-r1:1.5b
    echo.
    echo         To customise, copy the template:
    echo             copy .env.example .env
    echo             notepad .env
    echo.
)

REM --------------------------------------------------------------------
REM  Print URLs
REM --------------------------------------------------------------------

echo [info] Backend will start at http://%HOST%:%PORT%
echo.
echo   Chat UI:       http://%HOST%:%PORT%/
echo   Graph editor:  http://%HOST%:%PORT%/graph
echo   Settings:      http://%HOST%:%PORT%/settings
echo   PHP notes:     http://%HOST%:%PORT%/php/
echo.
echo   Press Ctrl+C to stop.
echo.

REM --------------------------------------------------------------------
REM  Export environment
REM --------------------------------------------------------------------

set "AGENT_HOST=%HOST%"
set "AGENT_PORT=%PORT%"
set "AGENT_RELOAD=%RELOAD%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

REM Prepend the portable runtime and PHP dirs to PATH.
set "PATH=%~dp0runtime\python;%~dp0runtime\php;%PATH%"

REM --------------------------------------------------------------------
REM  Hand off to the server launcher
REM --------------------------------------------------------------------
REM We use `call` rather than a direct invocation so that Ctrl+C is
REM delivered to the child Python process, and the batch file waits
REM for it to exit before cleaning up.

call "%PYTHON_BIN%" "%SERVE_SCRIPT%"

set "EXITCODE=%ERRORLEVEL%"

popd
endlocal & exit /b %EXITCODE%