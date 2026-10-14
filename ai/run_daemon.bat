@echo off
chcp 65001 >nul
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0daemon.py"
if "%errorlevel%"=="75" goto again
pause
exit /b
rem  exit code 75 = COMET asked to restart itself (upgrade apply/revert, restart.py) -> run again in this window
:again
echo [COMET] restarting with new code...
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0daemon.py"
if "%errorlevel%"=="75" goto again
pause
