# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  로그인하면 코멧 넘겨받기 (2026-10-15) — 시작 폴더 COMET_logon.vbs 가 창 없이 돌린다.
#   ① 로그인 전 코멧(세션 0)이 떠 있으면 STOP_FLAG 를 놓는다 → 그쪽이 답하던 건 마치고 스스로 내려감(강제 종료 안 함)
#   ② 이미 로그인 쪽 데몬이 떠 있으면 아무것도 안 함
#   ③ 아니면 평소처럼 run_daemon.bat 을 새 창(최소화)으로 → 목소리 · Alt 두 번 되는 코멧
#  기록은 logs/daemon-날짜.log 에 [로그인] 으로.
# ═══════════════════════════════════════════════════════════════
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import headless  # noqa: E402

HERE = headless.HERE
BAT = os.path.join(HERE, "run_daemon.bat")
PING = "http://127.0.0.1:8765/ping"
WAIT_STOP = 240          # 세션 0 코멧이 답하던 걸 마치는 데(대화 줄 최대 180초) + 여유


def log(msg):
    line = time.strftime("%H:%M:%S ") + f"[로그인] {msg}"
    print(line, flush=True)
    try:
        d = os.path.join(HERE, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"daemon-{time.strftime('%Y-%m-%d')}.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def daemon_up():
    try:
        urllib.request.urlopen(PING, timeout=3)
        return True
    except Exception:
        return False


def main():
    pid = headless.read_pid()
    if pid and headless.pid_alive(pid) and headless.session_id(pid) != headless.session_id():
        log(f"로그인 전 코멧(pid {pid}) 내려가라고 알림")
        with open(headless.STOP_FLAG, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        dead = time.time() + WAIT_STOP
        while time.time() < dead and (headless.pid_alive(pid) or daemon_up()):
            time.sleep(2)
        try:
            os.remove(headless.STOP_FLAG)
        except OSError:
            pass
        if headless.pid_alive(pid):
            log(f"로그인 전 코멧이 {WAIT_STOP}초 안에 안 내려감 — 그대로 둠(텔레그램 · 폰은 그쪽이 계속 받음)")
            return 1
        log("로그인 전 코멧 내려감")
    elif daemon_up():
        log("로그인 쪽 코멧이 이미 떠 있음 — 그대로")
        return 0
    subprocess.Popen(["cmd", "/c", "start", "COMET 데몬", "/min", BAT], cwd=HERE,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log("run_daemon.bat 띄움(최소화 창)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
