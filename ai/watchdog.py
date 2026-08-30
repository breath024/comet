# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 데몬 watchdog — '항상 켜져 있기'(상주 비서의 토대).
#   데몬(daemon.py, 127.0.0.1:8765)이 살아있는지 주기적으로 보고,
#   ★죽어 있을 때만★ 다시 띄운다. 살아있는 프로세스는 절대 건드리지 않는다
#   (kill·강제종료 없음 — 호윤 '파괴적 시스템 명령 금지' 준수).
#
#   - 헬스체크 = 127.0.0.1:8765 에 HTTP 요청 → 어떤 응답(200/401 등)이든 '살아있음'.
#     연결 거부/타임아웃이면 '죽음' → 새 콘솔로 daemon 재기동 후 부팅 유예.
#   - tailscale serve 는 setup_remote.bat 로 한 번 켜두면 재시작 넘어 유지됨.
#   - watchdog 자신은 중복 실행 방지(로컬 마커 포트 점유). 의존성 0.
#   - 부팅 자동시작은 setup_watchdog.bat(옵트인)로 등록. 여기선 등록 안 함.
# ═══════════════════════════════════════════════════════════════
import os
import sys
import time
import socket
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PY312 = r"C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe"
DAEMON = os.path.join(HERE, "daemon.py")
LOG = os.path.join(HERE, "watchdog.log")

HEALTH_URL = "http://127.0.0.1:8765/"
CHECK_SEC = 30          # 헬스체크 간격
BOOT_GRACE = 25         # 재기동 후 바인드까지 기다리는 시간
MARKER_PORT = 8766      # watchdog 중복 실행 방지용(데몬 8765 와 별개)


def _log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def is_daemon_alive(timeout=3):
    """8765 에 HTTP 요청 → 어떤 HTTP 응답이든 살아있음. 연결거부/타임아웃이면 죽음."""
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # 401/403 등 = 서버는 응답 = 살아있음
    except Exception:
        return False         # 연결 거부·타임아웃 = 죽음


def start_daemon():
    """데몬을 새 콘솔로 띄운다(기존 프로세스는 건드리지 않음 — 띄우기만)."""
    if not os.path.exists(PY312):
        _log(f"⚠️ Python 3.12 없음({PY312}) — 재기동 보류")
        return False
    try:
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        subprocess.Popen([PY312, DAEMON], cwd=HERE, creationflags=flags)
        _log("데몬이 죽어 있어 재기동함 (새 콘솔)")
        return True
    except Exception as e:
        _log(f"⚠️ 재기동 실패: {e}")
        return False


def _claim_single_instance():
    """watchdog 중복 실행 방지 — 마커 포트를 점유. 이미 돌면 None 반환."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", MARKER_PORT))
        s.listen(1)
        return s            # 살아있는 동안 들고 있어야 함
    except OSError:
        return None


def main():
    if _claim_single_instance() is None:
        print("watchdog 이미 실행 중 — 종료."); return
    _log(f"watchdog 시작 (체크 {CHECK_SEC}s, 죽었을 때만 재기동·kill 안 함)")
    while True:
        try:
            if is_daemon_alive():
                pass         # 살아있음 → 아무것도 안 함(건드리지 않음)
            else:
                if start_daemon():
                    time.sleep(BOOT_GRACE)   # 바인드 기다림(중복 기동 방지)
        except Exception as e:
            _log(f"루프 오류(무시하고 계속): {e}")
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
