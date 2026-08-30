@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\COMET_watchdog.vbs"
> "%VBS%" echo Set s = CreateObject("WScript.Shell")
>> "%VBS%" echo s.Run "%~dp0run_watchdog.bat", 0, False
echo.
echo [완료] 로그인 시 COMET watchdog 자동 시작 등록됨(숨김 실행).
echo   watchdog = 데몬이 죽으면 자동 재기동(살아있으면 안 건드림 - kill 안 함).
echo   끄려면 이 파일 삭제:
echo     "%VBS%"
echo   지금 바로 켜려면 run_watchdog.bat 실행.
echo.
pause
