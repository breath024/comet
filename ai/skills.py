# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 스킬 로더
#
#  클로드 코드의 스킬을 본뜬 것: skills/ 폴더의 .md 파일 하나 = 스킬 하나.
#  파일 맨 위 한 줄 "# 설명" 이 목록에 뜨는 한 줄 소개, 나머지 본문이 지침.
#  새 스킬을 만들고 싶으면 skills/ 에 .md 만 떨어뜨리면 끝(코드 수정 불필요).
# ═══════════════════════════════════════════════════════════════
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(HERE, "skills")


def _read(name):
    path = os.path.join(SKILL_DIR, name + ".md")
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def meta(name):
    """{desc, mode} — mode 는 'agent'(도구 루프) 또는 'chat'(그냥 대화).
       스킬 파일 어딘가에 <!-- mode: chat --> 가 있으면 chat, 없으면 agent."""
    body = _read(name)
    if body is None:
        return None
    desc = body.splitlines()[0].lstrip("# ").strip() if body else ""
    mode = "chat" if "mode: chat" in body else "agent"
    return {"desc": desc, "mode": mode}


def list_skills():
    """{name: 한 줄 설명} 사전."""
    out = {}
    if not os.path.isdir(SKILL_DIR):
        return out
    for fn in sorted(os.listdir(SKILL_DIR)):
        if fn.endswith(".md"):
            m = meta(fn[:-3])
            out[fn[:-3]] = m["desc"] if m else ""
    return out


def load(name):
    """스킬 본문(지침) 반환. 없으면 None."""
    return _read(name)
