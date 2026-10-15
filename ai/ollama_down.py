# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  공용 「ollama 꺼짐」 표시 (2026-10-14)
#
#  voice.py 의 한글 소리 쉬는 시간(HANGUL_PAUSE)을 넓힌 것.
#  ollama 가 아예 안 떠 있으면(연결 거부) 매 질문마다 문지기 → 본 모델 → light 재시도를
#  차례로 다 부딪친 뒤에야 클로드로 갔다. 연결 거부가 한 번 나면 표시를 켜고,
#  켜져 있는 동안은 모델을 안 부른다 — comet 은 바로 「로컬이 못 뜰 때 대신 답하는 클로드」로.
#
#  · 연결 거부만 켠다. VRAM 부족 · 500 · timed out 은 서버는 살아 있는 것이라 지금처럼(light 로 내리기 등).
#  · 켜져 있는 동안 뒤에서 PROBE_SEC 마다 한 번 /api/tags 를 2초로 확인 → 살아나면 바로 풀고 한 줄 남긴다.
#  · 꺼져 있으면 스스로 켠다(10/15 호윤 텔레그램 「올라마가 죽어있을 때 스스로 킬 수 있도록 고쳐」) — start_ollama().
#    표시가 켜질 때 한 번 + 꺼져 있는 동안 RESTART_SEC 마다. ollama 프로세스가 이미 있으면(올라오는 중) 안 띄운다.
#    로그인한 창 = 「ollama app.exe」(평소처럼 트레이 + serve) · 로그인 전(세션 0) = 「ollama.exe serve」. 끄지는 않는다.
#  · 주소는 ollama 파이썬 라이브러리와 같은 OLLAMA_HOST(없으면 127.0.0.1:11434) — comet·voice 의 Client 가 쓰는 곳.
# ═══════════════════════════════════════════════════════════════
import os
import re
import time
import threading
import urllib.request

PROBE_SEC = 30          # 꺼짐 표시가 켜져 있는 동안 살아났는지 보는 간격
PROBE_TIMEOUT = 2

#   httpx 는 윈도우에서 「[WinError 10061] … actively refused」, ollama 라이브러리는 그걸
#   「Failed to connect to Ollama. Please check that Ollama is downloaded, running …」 로 바꿔 던진다.
_REFUSED = re.compile(r"(10061|actively refused|connection refused|failed to connect to ollama)", re.I)

_lock = threading.Lock()
_down_since = 0.0       # 0 = 꺼짐 표시 없음
_watching = False       # 뒤에서 확인하는 스레드는 하나만


def _parse_host_copy(host):
    """ollama 라이브러리 _parse_host 를 그대로 옮긴 것 — 라이브러리에서 못 가져올 때만 쓴다.
    포트 없으면 스킴 따라 기본값(스킴 없음 11434 · http 80 · https 443), 경로는 남긴다."""
    import ipaddress
    import urllib.parse
    host, port = host or "", 11434
    scheme, _, hostport = host.partition("://")
    if not hostport:
        scheme, hostport = "http", host
    elif scheme == "http":
        port = 80
    elif scheme == "https":
        port = 443
    split = urllib.parse.urlsplit(f"{scheme}://{hostport}")
    host = split.hostname or "127.0.0.1"
    port = split.port or port
    try:
        if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
            host = f"[{host}]"
    except ValueError:
        pass
    path = split.path.strip("/")
    return f"{scheme}://{host}:{port}/{path}" if path else f"{scheme}://{host}:{port}"


def _base():
    """comet·voice 의 ollama.Client() 가 실제로 쓰는 주소 — 라이브러리의 정규화를 그대로 다시 쓴다."""
    try:
        from ollama._client import _parse_host
    except Exception:
        _parse_host = _parse_host_copy
    return _parse_host(os.environ.get("OLLAMA_HOST")).rstrip("/")


def is_refused(e):
    return bool(_REFUSED.search(str(e)))


def is_down():
    return _down_since > 0


def mark(e):
    """연결 거부면 꺼짐 표시를 켜고 True. 아니면 아무것도 안 하고 False."""
    global _down_since, _watching
    if not is_refused(e):
        return False
    with _lock:
        if not _down_since:
            _down_since = time.time()
            print(f"  · [ollama 꺼짐] 연결 거부({str(e)[:60]}) — 살아날 때까지 모델 안 부름, "
                  f"{PROBE_SEC}초마다 {_base()}/api/tags 확인")
        if not _watching:
            _watching = True
            threading.Thread(target=_watch, daemon=True, name="ollama-down-watch").start()
    threading.Thread(target=start_ollama, daemon=True, name="ollama-start").start()
    return True


OLLAMA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
RESTART_SEC = 300       # 꺼져 있는 동안 다시 띄워 보는 간격
_last_start = 0.0


def _ollama_running():
    import subprocess
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/NH"], capture_output=True, text=True,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "ollama.exe" in (r.stdout or "").lower()
    except Exception:
        return False


def start_ollama():
    """ollama 를 띄운다. 띄웠으면 True. 최근에 띄웠거나 이미 프로세스가 있으면(올라오는 중) 안 띄운다."""
    global _last_start
    import subprocess
    with _lock:
        if time.time() - _last_start < RESTART_SEC:
            return False
        _last_start = time.time()
    if os.name != "nt" or _alive() or _ollama_running():
        return False
    try:
        import headless
        if headless.HEADLESS:
            cmd = [os.path.join(OLLAMA_DIR, "ollama.exe"), "serve"]
        else:
            cmd = [os.path.join(OLLAMA_DIR, "ollama app.exe")]
        if not os.path.exists(cmd[0]):
            print(f"  · [ollama 켜기] 실행 파일 없음: {cmd[0]}")
            return False
        subprocess.Popen(cmd, cwd=OLLAMA_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
        print(f"  · [ollama 켜기] 꺼져 있어서 띄움: {os.path.basename(cmd[0])}")
        return True
    except Exception as e:
        print(f"  · [ollama 켜기] 실패: {e}")
        return False


def _alive():
    try:
        with urllib.request.urlopen(_base() + "/api/tags", timeout=PROBE_TIMEOUT) as r:
            return r.status == 200
    except Exception:
        return False


def _watch():
    global _down_since, _watching
    while True:
        time.sleep(PROBE_SEC)
        if _alive():
            with _lock:
                took = int(time.time() - _down_since) if _down_since else 0
                _down_since = 0.0
                _watching = False
            print(f"  · [ollama 살아남] {_base()} 응답 — 꺼짐 표시 풀고 로컬로 (꺼져 있던 시간 {took}초)")
            return
        start_ollama()                  # RESTART_SEC 안 지났으면 알아서 건너뜀
