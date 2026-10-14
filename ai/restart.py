# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  데몬 스스로 껐다 켜기 (2026-10-14 · 호윤 「업그레이드 반영할 때 스스로 꺼졌다 켜지게 해줘」)
#
#  「업그레이드 적용 · 되돌리기」가 성공하면 답을 보낸 뒤 데몬이 스스로 내려가고 새 코드로 다시 뜬다.
#  · run_daemon.bat 안에서 돌면(부모 cmd 명령줄에 run_daemon.bat) 종료 코드 75 로 나간다 → bat 이 같은 창에서 다시 띄움.
#    창이 새로 안 생기고 옛 창에 「계속하려면 아무 키나」도 안 남는다.
#  · 그 밖(watchdog 이 직접 띄운 것 등)이면 run_daemon.bat 을 새 창으로 띄우고 나간다 — 새 데몬은 뮤텍스를 8초 기다린다.
#  · 내려가기 전에:
#      ① ai/*.py 를 전부 컴파일해 본다 — 문법이 깨졌으면 안 내려간다(다시 못 뜨고 창만 죽는다)
#      ② 대화 줄(_lock)을 잡고 나간다 — 텔레그램 · 말걸기가 답하는 도중에 끊기지 않게(최대 WAIT_LOCK 초)
#      ③ 줄여 둔 다른 앱 볼륨을 되돌린다(덕킹) — os._exit 는 finally 를 안 돈다
#  · 다른 프로세스는 안 건드린다(아야카 목소리 서버 · 대화창은 그대로, 대화창은 저절로 다시 붙는다).
#  · 데몬이 아닌 콘솔 코멧(run.bat)에선 안 한다 — ENABLED 는 daemon.py 만 켠다.
# ═══════════════════════════════════════════════════════════════
import glob
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BAT = os.path.join(HERE, "run_daemon.bat")
LOOP_CODE = 75          # run_daemon.bat 이 이 코드면 다시 띄운다
WAIT_LOCK = 180
ENABLED = False         # daemon.py 가 켠다
LOCK = None             # daemon.py 가 대화 줄(_lock)을 꽂는다
_busy = threading.Lock()


def _flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def compile_check():
    """ai/*.py 전부 메모리에서만 컴파일(.pyc 안 씀). 깨진 파일 목록 — 비면 통과."""
    bad = []
    for f in sorted(glob.glob(os.path.join(HERE, "*.py"))):
        try:
            with open(f, "rb") as fp:
                compile(fp.read(), f, "exec")
        except Exception as e:
            bad.append(f"{os.path.basename(f)}: {str(e)[:120]}")
    return bad


def _in_loop_bat():
    """부모가 run_daemon.bat 을 돌리는 cmd 인가."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={os.getppid()}').CommandLine"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=20, creationflags=_flags())
        return "run_daemon.bat" in (r.stdout or "").lower()
    except Exception:
        return False


def _go(why, log):
    got = LOCK.acquire(timeout=WAIT_LOCK) if LOCK else False
    try:
        time.sleep(1.0)                       # 방금 보낸 답이 대화창에 다 닿게
        try:
            import voice
            voice.ducker.recover()
        except Exception:
            pass
        loop = _in_loop_bat()
        log(f"\n[재시작] {why} — {'같은 창에서' if loop else '새 창으로'} 새 코드로 다시 뜬다")
        sys.stdout.flush()
        sys.stderr.flush()
        if loop:
            os._exit(LOOP_CODE)
        subprocess.Popen(["cmd", "/c", BAT], cwd=HERE, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        os._exit(0)
    except Exception as e:                    # 못 내려가면 그대로 산다 — 손으로 재시작
        log(f"[재시작] 실패 — 그대로 둠: {e}")
        if got:
            LOCK.release()
        _busy.release()


def soon(why, delay=1.5, log=print):
    """뒤에서 곧 껐다 켠다. (했는지, 안 했으면 이유) — 문법이 깨졌거나 데몬이 아니면 안 함."""
    if not ENABLED:
        return False, "데몬이 아니라서(콘솔 코멧) — 손으로 다시 켜야 해"
    bad = compile_check()
    if bad:
        return False, "새 코드 문법이 깨져서 안 껐어 — " + " / ".join(bad[:3])
    if not _busy.acquire(blocking=False):
        return True, ""                       # 이미 껐다 켜는 중
    threading.Timer(delay, _go, args=(why, log)).start()
    return True, ""
