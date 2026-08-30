# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 콘솔 진입점
#
#  클로드 코드처럼 — 스킬을 골라(/code /debug /study /automate /strategy)
#  일을 시킨다. 코딩 스킬은 에이전트 루프(읽고·고치고·돌려보고·반복),
#  대화 스킬(전략)은 그냥 대화.
#
#  실행:  python coder.py            (현재 폴더가 작업 디렉터리)
#         python coder.py <경로>     (그 폴더를 작업 디렉터리로)
#  명령:  /skills  /code  /debug ...  /cd <경로>  /auto  /backend  /pwd  종료
# ═══════════════════════════════════════════════════════════════
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
except Exception:
    pass

import llm
import skills
import agent
import dev_tools

try:
    import comet as _comet          # 대화 모드 페르소나·프로필 재사용
    CHAT_SYSTEM = _comet.SYSTEM
except Exception:
    CHAT_SYSTEM = "너는 COMET, 호윤의 비서다. 침착하고 직설적으로, 근거로만 답한다. 한국어로 짧게."


# ── 에이전트 진행 렌더러 ─────────────────────────────────────────
def _render(kind, data):
    if kind == "step":
        th = (data.get("thought") or "").strip()
        print(f"  [{data['n']}] 🔧 {data['tool']}" + (f"  · {th}" if th else ""))
    elif kind == "result":
        r = data["result"]
        if r.get("skipped"):
            print(f"      ↳ 건너뜀(거부)")
        elif r.get("ok"):
            msg = r.get("msg") or (f"code={r.get('code')}" if "code" in r else "ok")
            print(f"      ↳ {msg}")
        else:
            print(f"      ↳ 실패: {r.get('msg', r)}")
    elif kind == "ask":
        print(f"\n❓ {data}")
    elif kind == "done":
        print(f"\n✅ {data}")
    elif kind == "final":
        print(f"\nCOMET: {data}")
    elif kind == "limit":
        print(f"\n⛔ {data}걸음 한도 도달 — 끝내지 못함.")
    elif kind == "loop":
        print(f"\n🔁 같은 동작 {data.get('times')}회 반복 — 무한루프 방지로 중단.")
    elif kind == "stuck":
        print(f"\n🧱 도구 {data}회 연속 실패 — 헛돌기 방지로 중단.")
    elif kind == "error":
        print(f"\n⚠ 모델 호출 오류 — 중단: {data}")
    elif kind == "escalate":
        print(f"\n⤴ 로컬이 막힘({data}) → API 모델로 승급 재시도 (비용 발생).")
    elif kind == "guard":
        print(f"\n🛡 비용 가드: {data}")


class Session:
    def __init__(self, root):
        self.skill = "debug"            # 기본 스킬
        self.auto = False
        self.box = dev_tools.ToolBox(root=root, auto_approve=False)
        self.chat_history = []
        self.pending = None        # ask/limit 로 멈춘 작업의 messages (이어가기용)

    def set_skill(self, name):
        m = skills.meta(name)
        if not m:
            print(f"  그런 스킬 없음: {name}  (/skills 로 목록)")
            return
        self.skill = name
        self.pending = None        # 스킬 바꾸면 이어가기 취소
        print(f"  스킬 → {name} ({m['mode']}) · {m['desc']}")

    def run_task(self, task):
        m = skills.meta(self.skill)
        prompt = skills.load(self.skill) or ""
        if m and m["mode"] == "chat":
            self.pending = None
            self._chat(prompt, task)
            return
        self.box.auto_approve = self.auto
        if self.pending:
            print(f"  ── 이어서({self.skill}) ──")
            res = agent.resume(self.pending, task, self.box, on_event=_render)
        else:
            print(f"  ── 에이전트({self.skill}) · auto={'ON' if self.auto else 'off'} ──")
            res = agent.run_smart(task, box=self.box, skill_prompt=prompt, on_event=_render)
            if res.get("escalated"):
                print("  (API 모델로 승급해 처리함)")
        if res.get("stopped") in ("ask", "limit"):
            self.pending = res.get("messages")
            print("  (답/추가 지시를 입력하면 이어서 진행, 다른 스킬로 바꾸면 취소)")
        else:
            self.pending = None

    def _chat(self, skill_prompt, task):
        system = CHAT_SYSTEM + "\n\n[현재 모드 지침]\n" + skill_prompt
        self.chat_history.append({"role": "user", "content": task})
        msgs = [{"role": "system", "content": system}] + self.chat_history[-20:]
        print("\nCOMET: ", end="", flush=True)
        full = llm.chat(msgs, temperature=0.4, stream=True,
                        on_token=lambda t: print(t, end="", flush=True))
        print()
        full = llm.strip_think(full)
        self.chat_history.append({"role": "assistant", "content": full})


BANNER = """═══════════════════════════════════════════════════
  COMET-Coder  ·  로컬 코딩 에이전트
═══════════════════════════════════════════════════"""


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    root = os.path.abspath(root)
    sess = Session(root)

    print(BANNER)
    backend, model = llm.active_backend()
    print(f"  백엔드: {backend} · 모델: {model}")
    print(f"  작업 폴더: {sess.box.root}")
    sk = skills.list_skills()
    print("  스킬: " + ", ".join(f"/{k}" for k in sk))
    print(f"  현재 스킬: /{sess.skill}   (명령: /skills /cd /auto /backend /pwd 종료)")

    while True:
        try:
            text = input(f"\n[{sess.skill}] 나: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n종료.")
            break
        if not text:
            continue

        low = text.lower()
        if low in ("종료", "exit", "quit"):
            break
        if low == "/skills":
            for k, d in skills.list_skills().items():
                print(f"  /{k:10s} {d}")
            continue
        if low == "/backend":
            b, m = llm.active_backend()
            print(f"  백엔드: {b} · 모델: {m}")
            continue
        if low == "/pwd":
            print(f"  작업 폴더: {sess.box.root}")
            continue
        if low == "/auto":
            sess.auto = not sess.auto
            print(f"  자동 승인: {'ON — 확인 없이 쓰기/실행(주의)' if sess.auto else 'off — 매번 확인'}")
            continue
        if low.startswith("/cd"):
            p = text[3:].strip().strip('"')
            np = os.path.abspath(p) if p else sess.box.root
            if os.path.isdir(np):
                sess.box.root = np
                print(f"  작업 폴더 → {np}")
            else:
                print(f"  그런 폴더 없음: {np}")
            continue
        if low.startswith("/council") or low.startswith("/회의"):
            t = text.split(" ", 1)
            t = t[1].strip() if len(t) > 1 else ""
            if not t:
                print("  사용법: /council <할 일>  (여러 에이전트가 회의해 결과 도출)")
                continue
            import council

            def _cev(kind, data):
                if kind == "role":
                    print(f"  ◆ {data['role']} [{data['brain']}] …")
                elif kind == "clean":
                    print(f"  ✓ 라운드 {data['round']}: 치명 결함 없음 → 조기 종료")
                elif kind == "done":
                    print(f"  ✔ 회의 완료 ({data['chars']}자 · {data.get('rounds', 1)}라운드)")
            out = council.council(t, root=sess.box.root, on_event=_cev)
            if out.get("ok"):
                cr = (out["transcript"].get("critic") or "").strip()
                print("\n🔎 반론 요지:\n" + cr[:400])
                print("\n✅ 최종:\n" + out["result"])
            else:
                print("  " + out.get("msg", "실패"))
            continue
        if text.startswith("/"):
            sess.set_skill(text[1:].split()[0])
            # /code <할 일> 처럼 뒤에 작업을 붙이면 바로 실행
            rest = text[1:].split(" ", 1)
            if len(rest) > 1 and rest[1].strip():
                sess.run_task(rest[1].strip())
            continue

        sess.run_task(text)


if __name__ == "__main__":
    main()
