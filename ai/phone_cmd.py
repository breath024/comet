# -*- coding: utf-8 -*-
"""전화 비서 점검 · 시험 명령 (2026-10-13) — 모델 안 거침.

  전화 점검 — adb · 블루투스 통화 장치 · 휴대폰과 연결 준비 상태
  전화 시험 — 점검이 다 O 면 호윤 아이폰으로 시험 전화(call_agent --test)를 뒤에서 걸고, 끝나면 배너 + 텔레그램

전화는 밖으로 나가는 일이라 텔레그램에서 온 말로는 안 건다(remote_guard) — 점검만 된다.
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
PHONE = os.path.join(_DIR, "phone")
NUMBERS_FILE = os.path.join(PHONE, "numbers.json")   # 진짜 번호는 여기만 — git 에 안 올림(공개 저장소)
TEST_GOAL = ("시험 통화: 받은 분은 정호윤 님 본인이다. AI 비서 시험 전화라고 밝히고, 제 목소리가 잘 들리는지 물어본 뒤 "
             "짧게 인사하고 끊기")
_busy = threading.Event()


def _test_number():
    """시험 받을 번호(호윤 아이폰 — 10/13 호윤 지정) = numbers.json 의 "test". 없거나 0000 이면 ""."""
    try:
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            n = re.sub(r"\D", "", str(json.load(f).get("test", "")))
    except (OSError, ValueError, AttributeError):
        return ""
    return "" if len(n) < 10 or not n[3:].strip("0") else n


def _ready():
    if PHONE not in sys.path:
        sys.path.insert(0, PHONE)
    import ready
    return ready.check()


def _start_test(number):
    os.makedirs(os.path.join(PHONE, "logs"), exist_ok=True)
    out_path = os.path.join(PHONE, "logs", time.strftime("%Y%m%d_%H%M%S") + "_run.out")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("ANTHROPIC_API_KEY", None)          # 키는 call_agent 가 파일에서만 읽는다(메모리 feedback_api_key_never_in_env)
    before = set(glob.glob(os.path.join(PHONE, "logs", f"*_{number}.log")))
    out = open(out_path, "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, os.path.join(PHONE, "call_agent.py"), number,
                          "--goal", TEST_GOAL, "--test", "--max-turns", "6"],
                         cwd=PHONE, env=env, stdout=out, stderr=subprocess.STDOUT)

    def wait():
        try:
            p.wait(timeout=600)
        except subprocess.TimeoutExpired:
            p.kill()
        finally:
            out.close()
            _busy.clear()
        new = sorted(set(glob.glob(os.path.join(PHONE, "logs", f"*_{number}.log"))) - before)
        if new:
            rows = open(new[-1], encoding="utf-8").read().strip().splitlines()
            body = "\n".join(r.split("] ", 1)[-1] for r in rows[-8:])
        else:
            body = f"통화 기록이 안 생김 — {out_path} 확인"
        try:
            import issues
            issues.popup("COMET · 전화 시험", "끝났어", "텔레그램에 요약", level=2, sec=8)
        except Exception as e:
            print(f"  [전화 시험 배너 오류] {e}")
        try:
            import notify
            notify.send("📞 전화 시험 끝\n" + body, html_mode=False)
        except Exception as e:
            print(f"  [전화 시험 텔레그램 오류] {e}")

    threading.Thread(target=wait, name="phone-test", daemon=True).start()


def command(t):
    """전화 점검 · 전화 시험이면 답, 아니면 None(대화로 흘림)."""
    key = t.replace(" ", "")
    if key in ("전화점검", "전화준비"):
        ok, lines = _ready()
        return "\n".join(lines) + ("\n→ 준비 됨. 「전화 시험」 하면 아이폰으로 걸어." if ok else "\n→ 준비 안 됨")
    if key in ("전화시험", "전화테스트", "시험전화"):
        import remote_guard
        if remote_guard.active():
            return "전화 거는 건 텔레그램에선 안 해 — PC 앞(콘솔이나 폰 웹)에서 「전화 시험」. 「전화 점검」은 여기서도 돼."
        if _busy.is_set():
            return "시험 전화 하나 돌고 있어. 끝나면 텔레그램으로 알려줄게."
        number = _test_number()
        if not number:
            return f'시험 받을 번호가 없어 — {NUMBERS_FILE} 에 {{"test": "010…"}} 넣어 줘(공개 저장소라 번호는 git 에 안 올림).'
        ok, lines = _ready()
        if not ok:
            return "\n".join(lines) + "\n→ 준비가 안 돼서 안 걸었어."
        _busy.set()
        try:
            _start_test(number)
        except Exception as e:
            _busy.clear()
            return f"시험 전화 못 띄움: {e}"
        return (f"아이폰({number[:3]}-{number[3:-4]}-{number[-4:]})으로 시험 전화 걸게. 받아서 두세 마디 해 봐 — 시험이라 본인 연결 대신 끊어. "
                "끝나면 텔레그램으로 요약 보낼게.")
    return None
