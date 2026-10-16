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
#    표시가 켜질 때 한 번 + 꺼져 있는 동안 RESTART_SEC 마다. 127.0.0.1:11434 가 열려 있으면 안 띄운다.
#    로그인 창 · 세션 0 모두 shutil.which("ollama") 로 찾은 것을 「serve」로 — PATH 에 없으면 로그만. 끄지는 않는다.
#    띄우고 START_WAIT 초 안에 답하면 표시를 바로 푼다. 데몬 시작 때도 한 번(10/16). 로그엔 세션 번호.
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


RESTART_SEC = 300       # 꺼져 있는 동안 다시 띄워 보는 간격
_last_start = 0.0


def _port_open():
    """127.0.0.1:11434 에 붙어 보기만 — 띄울지 말지는 이것 하나로 정한다(요청 ①)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=PROBE_TIMEOUT):
            return True
    except OSError:
        return False


START_WAIT = 20        # 띄운 뒤 /api/tags 가 답할 때까지 기다리는 최대 시간 — 답하면 꺼짐 표시를 바로 푼다


def _sid():
    """로그에 찍을 세션 번호 — 세션 0(로그인 전 headless 데몬)에서도 같은 코드가 돈다."""
    try:
        import headless
        return headless.session_id()
    except Exception:
        return "?"


def start_ollama():
    """ollama 를 띄운다. 띄웠으면 True. 최근(RESTART_SEC 안)에 시도했거나 127.0.0.1:11434 가 열려 있으면 안 띄운다.
       떠 있는 ollama 는 끄거나 다시 띄우지 않는다. 데몬 시작 때도 한 번(daemon.main)."""
    global _last_start, _down_since
    import shutil
    import subprocess
    with _lock:
        if time.time() - _last_start < RESTART_SEC:
            return False
        _last_start = time.time()
    if os.name != "nt":
        return False
    if _port_open():
        print(f"  · [ollama 켜기] 이미 켜짐 (세션 {_sid()})")
        return False
    found = shutil.which("ollama")              # PATH 에서 찾은 것만 — 로그인 창이든 세션 0 이든 「ollama serve」
    if not found:
        print(f"  · [ollama 켜기] 못 켬: PATH 에 ollama 없음 (세션 {_sid()})")
        return False
    cmd = [found, "serve"]
    try:
        subprocess.Popen(cmd, cwd=os.path.dirname(cmd[0]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as e:
        print(f"  · [ollama 켜기] 못 켬: {e} (세션 {_sid()})")
        return False
    end = time.time() + START_WAIT
    while time.time() < end:
        time.sleep(1)
        if _alive():
            with _lock:
                _down_since = 0.0              # 30초 확인을 안 기다리고 바로 로컬로 — 확인 스레드는 다음 바퀴에 알아서 빠진다
            print(f"  · [ollama 켜기] 켬: {os.path.basename(cmd[0])} (세션 {_sid()})")
            return True
    print(f"  · [ollama 켜기] 못 켬: {os.path.basename(cmd[0])} 띄웠는데 {START_WAIT}초 안에 /api/tags 응답 없음 "
          f"(세션 {_sid()}) — 꺼짐 표시는 그대로, {PROBE_SEC}초마다 계속 확인")
    return True


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
        with _lock:
            if not _down_since:         # start_ollama 가 띄우고 이미 풀었다
                _watching = False
                return
        if _alive():
            with _lock:
                took = int(time.time() - _down_since) if _down_since else 0
                _down_since = 0.0
                _watching = False
            print(f"  · [ollama 살아남] {_base()} 응답 — 꺼짐 표시 풀고 로컬로 (꺼져 있던 시간 {took}초)")
            return
        start_ollama()                  # RESTART_SEC 안 지났으면 알아서 건너뜀
