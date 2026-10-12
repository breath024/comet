# Phone Link(휴대폰과 연결)를 콕 집어 tel: 번호를 넘긴다 — PC 의 tel: 기본앱이 Lync 라서 그냥 열면 Lync 가 받는다.
param([Parameter(Mandatory=$true)][string]$Number)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.System.Launcher, Windows.System, ContentType = WindowsRuntime]
$null = [Windows.System.LauncherOptions, Windows.System, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$opt = New-Object Windows.System.LauncherOptions
$opt.TargetApplicationPackageFamilyName = 'Microsoft.YourPhone_8wekyb3d8bbwe'
$uri = New-Object System.Uri ("tel:" + $Number)
$op = [Windows.System.Launcher]::LaunchUriAsync($uri, $opt)
$t = $asTask.MakeGenericMethod([bool]).Invoke($null, @($op))
$t.Wait(10000) | Out-Null
"launched=" + $t.Result
