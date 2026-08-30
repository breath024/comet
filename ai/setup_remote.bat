@echo off
chcp 65001 >nul
echo ============================================================
echo   COMET 원격(HTTPS) 설정
echo ============================================================
echo.
echo [전제] 먼저 아래 주소에서 Serve 를 켜야 함 (한 번만):
echo   https://login.tailscale.com/f/serve?node=n3RJA1yex211CNTRL
echo.
echo tailscale serve 설정 중...
"C:\Program Files\Tailscale\tailscale.exe" serve --bg 8765
echo.
echo === 현재 serve 상태 ===
"C:\Program Files\Tailscale\tailscale.exe" serve status
echo.
echo 위에 https://...ts.net 주소가 보이면 성공.
echo 폰/외부에서 그 주소로 접속 (Tailscale 켜진 기기).
echo "Serve is not enabled" 가 뜨면 위 링크에서 먼저 Serve 를 켜라.
echo ============================================================
pause
