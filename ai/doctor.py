# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  코멧 닥터 — 데몬을 띄우고 지켜본다 (2026-10-15 · 호윤 「버그 터지면 코멧 닥터 이런걸로 돌려서 고칠 수 있게」)
#
#  업그레이드가 검토 · 시험을 통과하면 호윤 확인 없이 반영되니(upgrade._auto_apply), 반영한 코드로 데몬이
#  안 뜨거나 계속 죽으면 여기서 잡는다.
#  데몬이 죽어 있으면 코멧한테 말을 못 거니 데몬 바깥에서 돈다.
#    run_daemon.bat → doctor.py → daemon.py (같은 창 · 콘솔 그대로)
#  · 종료 코드 75 (restart.py 가 껐다 켜기) → 바로 다시 띄운다
#  · 0 (다른 데몬이 이미 떠 있음) · 3 (포트 막힘) · Ctrl+C → 그대로 끝 — 코드 탓이 아니다
#  · 그 밖 = 죽음
#      ① 뜬 지 STARTUP_SEC 안에 죽었고 RECENT_H 시간 안에 적용한 업그레이드가 있으면 → 그 업그레이드를 git 으로 되돌리고 다시 띄움
#      ② CRASH_WIN 안에 CRASH_MAX 번 죽으면 → 클로드 · 아스트라에게 고치게(upgrade.py --heal · 하루 HEAL_MAX 번)
#         → 검토 · 시험 통과면 저절로 반영, 아니면 이 창에 보여 주고 호윤이 y 를 쳐야 반영 → 다시 띄움
#      ③ 그래도 안 되면 텔레그램으로 알리고 멈춘다(창은 남겨 둠 · Enter 로 다시 해 봄)
#  · 데몬이 떠 있을 때 이상하면 대화창에 「닥터 <증상>」 — 그건 upgrade.doctor 가 받는다.
#  ★이 파일과 run_daemon.bat 은 업그레이드가 못 고친다(upgrade.EDIT_SYS · 자동 적용에서 뺌) — 지키는 쪽이 같이 깨지면 안 된다.
#  ★upgrade.py 가 깨져 있어도 되돌리기는 되게 git 과 상태 파일을 직접 만진다. 프로세스는 자기가 띄운 데몬만 기다린다.
# ═══════════════════════════════════════════════════════════════
import datetime as dt
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                         # git 저장소 뿌리 = COMET
PY = sys.executable
STATE = os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP", HERE), "comet_upgrade", "state.json")

LOOP_CODE = 75
STOP_CODES = {0, 3, 0xC000013A}                      # 정상 끝 · 포트 막힘 · Ctrl+C
STARTUP_SEC = 180
RECENT_H = 6
CRASH_WIN = 600
CRASH_MAX = 3
HEAL_MAX = 2
HEAL_TIMEOUT = 3600


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def log(msg):
    """창에 찍고 데몬 로그 파일에도 — 데몬이 죽어 있을 때 남는 건 이것뿐이다."""
    line = f"[닥터] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        d = os.path.join(HERE, "logs")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"daemon-{time.strftime('%Y-%m-%d')}.log"), "a", encoding="utf-8") as f:
            for l in line.splitlines():
                f.write(time.strftime("%H:%M:%S ") + l + "\n")
    except Exception:
        pass


def notify(text):
    """텔레그램 — 따로 띄운 파이썬으로(notify.py 가 깨졌어도 닥터는 안 죽게)."""
    try:
        subprocess.run([PY, "-c", "import sys, notify; notify.send(sys.argv[1], html_mode=False)", "🩺 코멧 닥터\n" + text],
                       cwd=HERE, timeout=60, capture_output=True)
    except Exception as e:
        log(f"텔레그램 못 보냄: {e}")


def _git(*args):
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def _log_tail(n=60):
    """오늘 데몬 로그 끝 n 줄 — 죽기 직전 Traceback 이 여기 있다(daemon._Tee 가 stderr 도 쓴다)."""
    try:
        p = os.path.join(HERE, "logs", f"daemon-{time.strftime('%Y-%m-%d')}.log")
        with open(p, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except Exception:
        return ""


def run_daemon():
    p = subprocess.Popen([PY, os.path.join(HERE, "daemon.py")], cwd=HERE)
    try:
        return p.wait()
    except KeyboardInterrupt:                        # Ctrl+C 는 데몬도 같이 받는다 — 내려가길 잠깐 기다리고 끝
        try:
            p.wait(timeout=10)
        except Exception:
            pass
        return 0xC000013A


def revert_recent():
    """RECENT_H 시간 안에 적용한 업그레이드를 되돌린다. 되돌렸으면 제목, 아니면 None."""
    st = _load()
    la = st.get("last_applied")
    if not la:
        return None
    try:
        age = dt.datetime.now() - dt.datetime.strptime(la.get("at", ""), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if age > dt.timedelta(hours=RECENT_H):
        return None
    if _git("merge-base", "--is-ancestor", la["commit"], "HEAD")[0] != 0:
        log(f"되돌릴 업그레이드 커밋({la['commit'][:7]})이 지금 코드에 없음 — 안 건드림")
        return None
    frm = la.get("from") or (la["commit"] + "~1")
    files = {p.strip() for p in _git("diff", "--name-only", frm, la["commit"])[1].splitlines() if p.strip()}
    dirty = {l[3:].strip().strip('"') for l in _git("status", "--porcelain")[1].splitlines()}
    if files & dirty:
        log("그 업그레이드 파일에 커밋 안 된 다른 수정이 있어 되돌리기 안 함: " + ", ".join(sorted(files & dirty)))
        return None
    code, out = _git("revert", "--no-commit", f"{frm}..{la['commit']}")
    if code:
        _git("revert", "--abort")
        log("되돌리다 충돌 — 그대로 둠: " + out.strip()[-200:])
        return None
    code, out = _git("commit", "-q", "-m", f"코멧 닥터 — 켜자마자 죽어서 업그레이드 되돌림: {la['title']}")
    if code:
        _git("revert", "--abort")
        log("되돌림 커밋 실패: " + out.strip()[-200:])
        return None
    st = _load()
    st["last_applied"] = None
    h = {"at": _now(), "title": la["title"], "status": "닥터가 되돌림", "commit": None}
    if la.get("items"):
        h["items"] = la["items"]
    st["history"] = (st.get("history") or [])[-29:] + [h]
    _save(st)
    return la["title"]


def heal(tail):
    """클로드 · 아스트라에게 고치게 — upgrade.py --heal(복사본에서 고치고 시험 · 검토 → 호윤이 y 면 반영). 반영했으면 True."""
    st = _load()
    today = time.strftime("%Y-%m-%d")
    d = st.get("doctor") or {}
    n = d.get("n", 0) if d.get("date") == today else 0
    if n >= HEAL_MAX:
        log(f"오늘 고치기를 이미 {n}번 해서 더 안 함")
        return False
    st["doctor"] = {"date": today, "n": n + 1}
    _save(st)
    log("클로드 · 아스트라에게 고치게 함 — 몇 분~20분")
    try:
        r = subprocess.run([PY, os.path.join(HERE, "upgrade.py"), "--heal", tail[-6000:]], cwd=HERE, timeout=HEAL_TIMEOUT)
        return r.returncode == 0
    except Exception as e:
        log(f"고치기 못 돌림: {e}")
        return False


def main():
    crashes = []
    while True:
        t0 = time.time()
        code = run_daemon()
        if code == LOOP_CODE:
            print("[COMET] 새 코드로 다시 띄움...", flush=True)
            continue
        if code in STOP_CODES:
            return code
        up = int(time.time() - t0)
        tail = _log_tail()
        log(f"데몬이 죽음 — 종료 코드 {code}, 뜬 지 {up}초")
        crashes = [c for c in crashes if time.time() - c < CRASH_WIN] + [time.time()]
        if up < STARTUP_SEC:
            title = revert_recent()
            if title:
                log(f"방금 적용한 업그레이드를 되돌리고 다시 띄움: {title}")
                notify(f"켜자마자 죽어서 방금 적용한 업그레이드를 되돌렸어: {title}\n옛 코드로 다시 띄움.\n"
                       f"죽기 직전 로그:\n{tail[-800:]}")
                continue
        if len(crashes) < CRASH_MAX:
            wait = 5 * len(crashes)
            log(f"{wait}초 뒤 다시 띄움 ({len(crashes)}/{CRASH_MAX})")
            time.sleep(wait)
            continue
        notify(f"데몬이 {CRASH_WIN // 60}분 안에 {len(crashes)}번 죽어서 고치는 중이야. 다 고치면 PC 데몬 창에서 "
               f"반영할지 물어볼게.\n죽기 직전 로그:\n{tail[-800:]}")
        if heal(tail):
            log("고친 걸 반영함 — 다시 띄움")
            notify("고친 걸 반영하고 데몬을 다시 띄웠어. 이상하면 「업그레이드 되돌리기」.")
            crashes = []
            continue
        log("못 고침 — 멈춤. 이 창에서 Enter 를 누르면 다시 띄워 본다(창을 닫으면 끝)")
        notify("데몬을 못 살렸어 — PC 데몬 창에서 Enter 를 누르면 다시 띄워 보고, 안 되면 클로드 코드로 봐야 해.\n"
               f"죽기 직전 로그:\n{tail[-800:]}")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            return 1
        crashes = []


if __name__ == "__main__":
    sys.exit(main())
