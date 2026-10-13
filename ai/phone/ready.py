"""전화 비서를 돌릴 준비가 됐는지 — adb · 블루투스 핸즈프리 · 휴대폰과 연결 세 가지를 본다.

    python ready.py        → 줄마다 O/X/? 와 할 일

adb 가 끊겨 있으면 무선 디버깅으로 페어링해 둔 폰을 mDNS 로 찾아 다시 붙여 본다(폰 무선 디버깅이 켜져 있어야 함).
"""
import re
import subprocess

ADB = r"C:\Users\USER\AppData\Local\Android\Sdk\platform-tools\adb.exe"


def _run(args, timeout=15):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace").stdout
    except Exception as e:
        return f"(실행 실패: {e})"


def adb_devices():
    out = _run([ADB, "devices"])
    return [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("\tdevice")]


def adb_reconnect():
    """페어링된 폰을 mDNS(_adb-tls-connect)로 찾아 adb connect. 붙인 주소 목록."""
    got = []
    for m in re.finditer(r"_adb-tls-connect\._tcp\.?\s+(\d+\.\d+\.\d+\.\d+:\d+)", _run([ADB, "mdns", "services"])):
        if "connected" in _run([ADB, "connect", m.group(1)]):
            got.append(m.group(1))
    return got


def hfp_status():
    """S26+ 블루투스 통화 장치(AudioEndpoint) 상태들 — 블루투스가 붙어 있으면 OK(10/12 실측)."""
    ps = ("Get-PnpDevice -Class AudioEndpoint | Where-Object { $_.FriendlyName -match 'S26' } | "
          "ForEach-Object { $_.Status }")
    return _run(["powershell", "-NoProfile", "-Command", ps]).split()


def phonelink_state():
    """'off' = 폰의 「Windows와 연결」 꺼짐 문구 · 'ok' = 창은 있고 그 문구 없음 · 'closed' = 창이 안 떠 있어 못 봄."""
    import uiautomation as auto
    import call_agent as ca
    with auto.UIAutomationInitializerInThread():        # 데몬은 요청마다 다른 스레드라 COM 을 여기서 연다
        if not ca._pl_windows():
            return "closed"
        if ca._pl_find(lambda c: "꺼져 있습니다" in (c.Name or ""), kind="TextControl"):
            return "off"
        return "ok"


def check():
    """(다 됐나, 줄 목록). 줄 = 'O …' / 'X … → 할 일' / '? …'. ?(못 봄)는 막지 않는다."""
    lines, ok = [], True

    devs = adb_devices()
    if not devs:
        again = adb_reconnect()
        devs = adb_devices() if again else []
    if devs:
        lines.append(f"O 폰 무선 연결(adb) — {devs[0]}")
    else:
        ok = False
        lines.append("X 폰 무선 연결(adb) 안 됨 → 폰 개발자 옵션 「무선 디버깅」 켜고 PC 와 같은 와이파이")

    st = hfp_status()
    if st and all(s == "OK" for s in st):
        lines.append("O 블루투스 통화 장치(S26+ 핸즈프리)")
    else:
        ok = False
        lines.append(f"X 블루투스 통화 장치 {('상태 ' + '/'.join(st)) if st else '없음'} → 폰 블루투스 켜고 PC 와 연결")

    try:
        pl = phonelink_state()
    except Exception as e:
        pl = f"err {e}"
    if pl == "ok":
        lines.append("O 휴대폰과 연결 창")
    elif pl == "off":
        ok = False
        lines.append("X 폰의 「Windows와 연결」 꺼짐 → 폰 빠른 설정에서 켜고 휴대폰과 연결 창 「다시 시도」")
    else:
        lines.append("? 휴대폰과 연결 창이 안 떠 있어 「Windows와 연결」은 못 봄 — 걸 때 막히면 그 이유가 기록에 남음")
    return ok, lines


if __name__ == "__main__":
    ok, lines = check()
    print("\n".join(lines))
    print("준비 됨" if ok else "준비 안 됨")
