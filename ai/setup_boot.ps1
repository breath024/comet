# 코멧을 로그인 없이 켜기 (2026-10-15) — 관리자 PowerShell 로 한 번. 설명은 headless.py 머리말.
#  ① 작업 스케줄러 COMET-boot : 부팅 30초 뒤 doctor.py 를 세션 0 에서(S4U = 비번 저장 없음 · 로그인 안 해도 돎)
#  ② 시작 폴더 COMET_logon.vbs : 로그인하면 comet_logon.py 가 넘겨받기
#  ③ 세션 0 시험(COMET-boot-selftest 한 번 돌리고 지움) → logs\headless_selftest.txt
#  되돌리기: Unregister-ScheduledTask COMET-boot -Confirm:$false  +  시작 폴더 COMET_logon.vbs 삭제
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py   = 'C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe'
$user = "$env:USERDOMAIN\$env:USERNAME"
$out  = Join-Path $here 'logs\setup_boot_result.txt'
New-Item -ItemType Directory -Force (Join-Path $here 'logs') | Out-Null
try {
    $p = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
    $t = New-ScheduledTaskTrigger -AtStartup
    $t.Delay = 'PT30S'
    $a = New-ScheduledTaskAction -Execute $py -Argument "`"$here\doctor.py`"" -WorkingDirectory $here
    Register-ScheduledTask -TaskName 'COMET-boot' -Action $a -Trigger $t -Principal $p -Settings $s -Force `
        -Description '코멧 로그인 전 실행(텔레그램 · 폰 웹). 로그인하면 comet_logon.py 가 넘겨받음' | Out-Null

    $vbs = Join-Path ([Environment]::GetFolderPath('Startup')) 'COMET_logon.vbs'
    $pyw = $py -replace 'python\.exe$', 'pythonw.exe'
    "Set s = CreateObject(`"WScript.Shell`")`r`ns.Run `"`"`"$pyw`"`" `"`"$here\comet_logon.py`"`"`", 0, False" |
        Set-Content -Encoding Unicode $vbs   # ASCII 면 경로 속 한글(개인프젝)이 ? 로 깨짐

    $a2 = New-ScheduledTaskAction -Execute $py -Argument "`"$here\headless.py`" --selftest" -WorkingDirectory $here
    Register-ScheduledTask -TaskName 'COMET-boot-selftest' -Action $a2 -Principal $p -Settings $s -Force | Out-Null
    Start-ScheduledTask 'COMET-boot-selftest'
    $dead = (Get-Date).AddSeconds(150)
    while ((Get-Date) -lt $dead -and (Get-ScheduledTask 'COMET-boot-selftest').State -eq 'Running') { Start-Sleep 2 }
    $info = Get-ScheduledTaskInfo 'COMET-boot-selftest'
    Unregister-ScheduledTask 'COMET-boot-selftest' -Confirm:$false
    "OK COMET-boot 등록 · vbs=$vbs · selftest 결과코드=$($info.LastTaskResult)" | Set-Content -Encoding UTF8 $out
} catch {
    "FAIL $($_.Exception.Message)" | Set-Content -Encoding UTF8 $out
}
