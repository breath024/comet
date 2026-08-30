@echo off
chcp 65001 >nul
REM COMET-Coder 런처 — 폴더를 이 파일에 드래그하면 그 폴더가 작업 폴더가 됩니다.
REM 그냥 더블클릭하면 바탕화면이 작업 폴더.
set "WORK=%~1"
if "%WORK%"=="" set "WORK=%USERPROFILE%\Desktop"
"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0coder.py" "%WORK%"
pause
