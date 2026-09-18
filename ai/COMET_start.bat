@echo off
chcp 65001 >nul
rem ── COMET 한 번에 켜기 ──────────────────────────────────────
rem  데몬(8765)이 이미 떠 있으면 대화창만 띄운다.
rem  ★본문은 일부러 ASCII 위주 — .bat 안 한글은 콘솔 코드페이지 따라 깨진다(작업 유의사항).
cd /d "%~dp0"

netstat -ano | findstr /r /c:"127.0.0.1:8765 .*LISTENING" >nul
if errorlevel 1 (
    echo [COMET] starting daemon...
    start "COMET daemon" "%~dp0run_daemon.bat"
    rem  데몬이 포트를 잡고 대화창이 붙을 때까지 잠깐 기다린다
    timeout /t 6 /nobreak >nul
) else (
    echo [COMET] daemon already running.
)

echo [COMET] opening chat window...
start "" "C:\Users\USER\AppData\Local\Programs\Python\Python312\pythonw.exe" "%~dp0chat_win.py"
