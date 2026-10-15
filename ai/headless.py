# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  로그인 전 코멧 (2026-10-15 · 호윤 「테일스케일처럼 로그인 안 해도 바로 켜지게」)
#
#  10/15 정전 뒤 PC 는 혼자 켜졌는데 로그인 화면에서 멈춰 코멧이 안 떴다(자동 시작이 하나도 없었음)
#  + 15분 뒤 절전 → 밖에서 아예 못 닿음. 그래서:
#  · 부팅 = 작업 스케줄러 COMET-boot(S4U · 비번 저장 없음)가 doctor.py 를 세션 0(화면 없음)에서 띄운다.
#    세션 0 에선 마이크 · 스피커 · 키보드 훅 · 화면이 없다 → 목소리 · Alt 두 번(talk) · 부르면 듣기(wake) 는 안 켠다.
#    텔레그램 · 폰 웹(tailscale serve → 127.0.0.1:8765) · 브리핑은 그대로.
#  · 로그인 = 시작 폴더 COMET_logon.vbs → comet_logon.py 가 세션 0 코멧에 STOP_FLAG 를 놓아 스스로 내려가게 하고
#    (강제 종료 안 함) 평소처럼 run_daemon.bat 을 띄운다.
#  · 절전 = 데몬이 떠 있는 동안만 막는다(keep_awake) — 끄면 윈도우 설정 그대로(호윤 고름).
#  ★Local\ 뮤텍스(daemon._only_one)는 세션마다 따로라 세션 0 코멧과 로그인 코멧이 서로를 못 본다 — 막는 건 포트(종료 3)뿐.
#  setup_boot.ps1 이 등록한다(관리자 승인 한 번). 시험: python headless.py --selftest
# ═══════════════════════════════════════════════════════════════
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STOP_FLAG = os.path.join(HERE, "headless_stop.flag")
PID_FILE = os.path.join(HERE, "headless.pid")
NO_START = {"talk", "wake"}          # 세션 0 에서 안 켜는 자율모드(마이크 · 키보드)


def session_id(pid=None):
    if os.name != "nt":
        return -1
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    sid = wintypes.DWORD()
    if not k32.ProcessIdToSessionId(pid or k32.GetCurrentProcessId(), ctypes.byref(sid)):
        return -1
    return sid.value


# COMET_HEADLESS=1 = 로그인한 창에서 세션 0 흉내(시험용)
HEADLESS = session_id() == 0 or os.environ.get("COMET_HEADLESS") == "1"


def user_logged_on():
    """콘솔 세션에 누가 로그인해 있나. 로그인 화면이면 False. 모르면 False(부팅 코멧이 뜨는 쪽으로)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        wts = ctypes.windll.wtsapi32
        con = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
        if con == 0xFFFFFFFF:
            return False
        buf, n = wintypes.LPWSTR(), wintypes.DWORD()
        if not wts.WTSQuerySessionInformationW(None, con, 5, ctypes.byref(buf), ctypes.byref(n)):   # 5 = WTSUserName
            return False
        name = buf.value or ""
        wts.WTSFreeMemory(buf)
        return bool(name.strip())
    except Exception:
        return False


def keep_awake():
    """이 스레드가 사는 동안 유휴 절전을 막는다(화면 꺼짐은 그대로). 데몬 main 스레드에서 부른다."""
    if os.name != "nt":
        return False
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))


def pid_alive(pid):
    if os.name != "nt" or not pid:
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000 | 0x00100000, False, int(pid))    # QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102             # WAIT_TIMEOUT = 아직 삶
    finally:
        k32.CloseHandle(h)


def read_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except Exception:
        return 0


def _selftest():
    """작업 스케줄러가 세션 0 에서 돌려 보는 것 — 화면 없는 쪽에서 뭐가 되는지 logs/headless_selftest.txt 로."""
    import shutil
    import subprocess
    import time
    import urllib.request
    out = [time.strftime("%Y-%m-%d %H:%M:%S"),
           f"session={session_id()} headless={HEADLESS} user_logged_on={user_logged_on()}",
           f"user={os.environ.get('USERNAME')} home={os.path.expanduser('~')}",
           f"python={sys.executable}"]
    for exe in ("claude", "codex"):
        p = shutil.which(exe)
        ver = ""
        if p:
            try:
                r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=60,
                                   encoding="utf-8", errors="replace")
                ver = (r.stdout or r.stderr).strip().splitlines()[0][:80] if (r.stdout or r.stderr) else f"code {r.returncode}"
            except Exception as e:
                ver = f"실패 {e}"
        out.append(f"{exe}={p or '없음'} {ver}")
    for name, url in (("ollama", "http://127.0.0.1:11434/api/tags"), ("daemon", "http://127.0.0.1:8765/ping")):
        try:
            urllib.request.urlopen(url, timeout=3)
            out.append(f"{name}=응답")
        except Exception as e:
            out.append(f"{name}=없음 ({str(e)[:60]})")
    os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
    with open(os.path.join(HERE, "logs", "headless_selftest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
