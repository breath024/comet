# -*- coding: utf-8 -*-
"""코멧이 대화 밖으로 내보낸 것 기록 (2026-10-16).

왜: 코멧은 대화 기록(comet_history.json)에 든 말만 안다. 그런데 텔레그램 브리핑 · 소식 알림 · 업그레이드 알림 ·
    할일 알림 · 보낸 파일 · 화면에 띄운 소식은 기록 밖에서 나간다 → 호윤이 「이거 말고 반전 버전으로」라고 하면
    「이거」를 몰랐다(10/16 16:39 로고 사진 뒤). 호윤: 「출력된 거 기준으로 안 봐서 그런가 봐 · 콘솔하고 텔레그램 사이 간극」.

notify.send · send_file · issues._post_feed 가 여기에 한 줄씩 적고, comet.History.recent() 가 최근 것을 대화 앞에 붙인다.
프로세스가 여럿이라(데몬 · 닥터 · 맡기기 시험) 메모리 말고 파일에 한 줄씩 덧붙인다.
"""
import json
import os
import threading
import time

_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(_DIR, "outbox.jsonl")
KEEP = 200            # 줄 수가 이걸 두 배 넘으면 뒤쪽 KEEP 줄만 남긴다
_lock = threading.Lock()


def record(channel, text):
    text = (text or "").strip()
    if not text:
        return
    line = json.dumps({"t": time.time(), "ch": channel, "text": text[:700]}, ensure_ascii=False)
    try:
        with _lock:
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if os.path.getsize(PATH) > 400_000:
                _trim()
    except Exception:
        pass              # 기록 못 해도 보내기는 막지 않는다


def _trim():
    with open(PATH, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > KEEP * 2:
        tmp = PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-KEEP:])
        os.replace(tmp, PATH)


def recent(hours=6, n=6):
    try:
        with open(PATH, encoding="utf-8") as f:
            lines = f.readlines()[-60:]
    except Exception:
        return []
    cut = time.time() - hours * 3600
    out = []
    for ln in lines:
        try:
            x = json.loads(ln)
        except Exception:
            continue
        if x.get("t", 0) >= cut:
            out.append(x)
    return out[-n:]


def context(hours=6, n=6):
    """대화 앞에 붙일 글 — 없으면 ""."""
    items = recent(hours, n)
    if not items:
        return ""
    rows = [f"[{time.strftime('%H:%M', time.localtime(x['t']))} {x['ch']}] {x['text'][:300]}" for x in items]
    return ("(대화 기록 밖에서 내가 내보낸 것 — 오래된 것부터. 호윤이 「이거 · 그거 · 방금 보낸 거 · 아까 알림」이라고 하면 "
            "이걸 가리킬 수 있다. 묻지 않으면 먼저 꺼내지 마라.)\n" + "\n".join(rows))
