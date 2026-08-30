# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 다리 모듈 (comet/daemon ↔ 코딩 에이전트)
#
#  comet.py 에서 "코딩 ..." 명령을 이 함수로 넘긴다. comet 을 import 하지 않으므로
#  순환 의존이 없다. 데몬(폰)에서도 같은 함수를 타므로 한 곳만 손대면 콘솔·폰 동시 적용.
#
#  데몬은 1요청=1응답이라 대화형 y/n 확인이 불가능 → auto_approve=True 로 돌되,
#  모든 쓰기는 .comet_bak 에 자동 백업되어 되돌릴 수 있다(안전망).
#  진행 과정은 텍스트 트레이스로 모아 폰 화면에 그대로 보여준다.
# ═══════════════════════════════════════════════════════════════
import os

import agent
import dev_tools
import skills

HOME = os.path.expanduser("~")
DEFAULT_ROOT = os.path.join(HOME, "Desktop")

# "코딩/code", "코딩/debug" 처럼 스킬 지정 가능. 없으면 범용(스킬 없이 기본 프로토콜).
_SKILL_ALIAS = {"code": "code", "debug": "debug", "study": "study",
                "automate": "automate", "auto": "automate"}


def parse(rest):
    """'코딩' 뒤 문자열을 (root, skill, task) 로 분해.
       지원: '@C:\\경로' 로 작업폴더, '/code' 같은 스킬 접두."""
    root, skill = DEFAULT_ROOT, None
    toks = rest.strip().split(" ", 1)
    # @경로
    while toks and toks[0].startswith("@"):
        cand = toks[0][1:].strip('"')
        if os.path.isdir(cand):
            root = cand
        toks = toks[1].split(" ", 1) if len(toks) > 1 else [""]
    # /skill 또는 skill:  (council/회의 = 멀티에이전트 회의 모드)
    if toks and toks[0]:
        head = toks[0].lstrip("/").rstrip(":").lower()
        if head in ("council", "회의"):
            skill = "council"
            toks = toks[1].split(" ", 1) if len(toks) > 1 else [""]
        elif head in _SKILL_ALIAS:
            skill = _SKILL_ALIAS[head]
            toks = toks[1].split(" ", 1) if len(toks) > 1 else [""]
    task = " ".join(toks).strip() if isinstance(toks, list) else str(toks)
    return root, skill, task


def run_council_text(task, root=None):
    """멀티에이전트 회의(council) 실행 → 진행 + 반론요지 + 최종해답 텍스트."""
    import council
    root = root or DEFAULT_ROOT
    lines = [f"🧠 council 회의 · 폴더={root}"]

    def ev(kind, data):
        if kind == "role":
            lines.append(f"  ◆ {data['role']} [{data['brain']}]")
        elif kind == "clean":
            lines.append(f"  ✓ 라운드 {data['round']}: 치명 결함 없음 → 조기 종료")
        elif kind == "done":
            lines.append(f"  ✔ 회의 완료 ({data['chars']}자 · {data.get('rounds', 1)}라운드)")
    try:
        out = council.council(task, root=root, on_event=ev)
    except Exception as e:
        return "\n".join(lines + [f"[오류] {e}"])
    if not out.get("ok"):
        return "\n".join(lines + [out.get("msg", "실패")])
    crit = (out["transcript"].get("critic") or "").strip()
    lines.append("\n🔎 반론·검증 요지:\n" + (crit[:300] + ("…" if len(crit) > 300 else "")))
    lines.append("\n✅ 최종 해답:\n" + out["result"])
    return "\n".join(lines)


def _make_ev(lines):
    """이벤트 → lines 에 추가하는 공통 핸들러."""
    def ev(kind, data):
        if kind == "step":
            th = (data.get("thought") or "").strip()
            lines.append(f"[{data['n']}] {data['tool']}" + (f" — {th[:80]}" if th else ""))
        elif kind == "result":
            r = data["result"]
            if r.get("skipped"):
                lines.append("   ↳ 건너뜀(거부)")
            elif r.get("ok"):
                lines.append("   ↳ " + (r.get("msg") or (f"code={r.get('code')}" if "code" in r else "ok")))
            else:
                lines.append("   ↳ 실패: " + str(r.get("msg", r))[:120])
        elif kind == "ask":
            lines.append("❓ " + str(data))
        elif kind == "done":
            lines.append("✅ " + str(data))
        elif kind == "final":
            lines.append(str(data))
        elif kind == "limit":
            lines.append(f"⛔ {data}걸음 한도 — 끝내지 못함")
        elif kind == "loop":
            lines.append(f"🔁 같은 동작 {data.get('times')}회 반복 — 무한루프 방지 중단")
        elif kind == "stuck":
            lines.append(f"🧱 도구 {data}회 연속 실패 — 중단")
        elif kind == "error":
            lines.append(f"⚠ 모델 호출 오류 — 중단: {data}")
        elif kind == "escalate":
            lines.append(f"⤴ 로컬 막힘({data}) → API 승급 재시도(비용 발생)")
        elif kind == "guard":
            lines.append(f"🛡 비용 가드: {data}")
    return ev


def run_text(task, root=None, skill=None, auto_approve=True, max_steps=20):
    """코딩 에이전트를 돌리고, 진행 과정 + 결과를 사람이 읽을 텍스트로 반환."""
    return run_text_full(task, root=root, skill=skill, auto_approve=auto_approve,
                         max_steps=max_steps)["text"]


def run_text_full(task, root=None, skill=None, auto_approve=True, max_steps=20):
    """run_text 와 동일하되 멈춤 상태(stopped, messages, box)도 함께 반환.
       stopped="ask" 일 때 resume_text() 로 이어받을 수 있다."""
    if skill == "council":
        return {"text": run_council_text(task, root), "stopped": "done",
                "box": None, "messages": None}
    root = root or DEFAULT_ROOT
    box = dev_tools.ToolBox(root=root, auto_approve=auto_approve)
    prompt = skills.load(skill) if skill else ""
    lines = [f"🛠 코딩 에이전트 · 폴더={root}" + (f" · 스킬={skill}" if skill else "")]
    try:
        res = agent.run_smart(task, box=box, skill_prompt=prompt,
                              on_event=_make_ev(lines))
        if res.get("escalated"):
            lines.append("(API 모델로 승급해 처리함)")
    except Exception as e:
        lines.append(f"[오류] {e}")
        return {"text": "\n".join(lines), "stopped": "error", "box": box, "messages": None}
    return {"text": "\n".join(lines), "stopped": res.get("stopped", "done"),
            "box": box, "messages": res.get("messages")}


def resume_text(reply, box, messages, auto_approve=True):
    """ask 로 멈춘 코딩 세션을 사용자 답변(reply)으로 이어서 실행."""
    if box is not None:
        box.auto_approve = auto_approve
    lines = ["🔄 코딩 재개"]
    try:
        res = agent.resume(messages, reply, box=box, on_event=_make_ev(lines))
        if res.get("escalated"):
            lines.append("(API 모델로 승급해 처리함)")
    except Exception as e:
        lines.append(f"[오류] {e}")
        return {"text": "\n".join(lines), "stopped": "error", "box": box, "messages": None}
    return {"text": "\n".join(lines), "stopped": res.get("stopped", "done"),
            "box": box, "messages": res.get("messages")}
