@echo off
setlocal
rem ============================================================
rem  COMET one-click start  (daemon + chat window)
rem
rem  If the daemon (port 8765) is already up, only the chat window opens.
rem
rem  NOTE: kept ASCII on purpose.
rem    - Korean text in a .bat breaks depending on the console codepage.
rem    - 'timeout' dies with "Input redirection is not supported" when stdin
rem      is redirected, which is how this failed on 2026-09-18. We poll the
rem      port with ping-based waits instead, so there is no fixed sleep and
rem      no dependency on stdin.
rem ============================================================
cd /d "%~dp0"

set "PY=C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe"
if not exist "%PY%" (
    echo [COMET] python not found: %PY%
    pause
    exit /b 1
)

call :is_up
if "%UP%"=="1" (
    echo [COMET] daemon already running.
    goto :chat
)

if not exist "%~dp0run_daemon.bat" (
    echo [COMET] run_daemon.bat missing.
    pause
    exit /b 1
)

echo [COMET] starting daemon...
start "COMET daemon" "%~dp0run_daemon.bat"

rem  wait for the port, up to ~30s
set /a TRIES=0
:wait
call :is_up
if "%UP%"=="1" goto :chat
set /a TRIES+=1
if %TRIES% GEQ 30 (
    echo [COMET] daemon did not open port 8765. See the "COMET daemon" window.
    pause
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :wait

:chat
echo [COMET] opening chat window...
start "" "%PY%" "%~dp0chat_win.py"
exit /b 0

:is_up
set "UP=0"
netstat -ano -p TCP | findstr /r /c:"127\.0\.0\.1:8765 .*LISTENING" >nul 2>&1
if not errorlevel 1 set "UP=1"
goto :eof
