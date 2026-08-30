# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · council(회의) 엔진 — 멀티 에이전트 분업·토론
#
#  한 작업(코딩/로직)을 역할 나눠 회의시켜 결과를 도출한다.
#   설계자 → 구현자 → 반론·검증관 → 합성자.
#  사상: analyst 의 2-패스 이중판단(컨센서스↔역발상)이 할루시네이션·약점을
#        잡아 품질을 올린 게 검증됨 → 그 패턴을 코딩용 N역할로 일반화.
#
#  하이브리드 두뇌(호윤 선택): 평소 로컬(ollama), '핵심 역할(반론·합성)'만
#   클라우드 키 있으면 승급(클로드/GPT/DeepSeek). 키 없으면 전부 로컬(월 0원).
#   ※ 로컬 GPU 1장이라 회의는 '동시'가 아니라 '순차' — 역할별로 차례로 돈다(느림).
#
#  의존성: llm(로컬+API), cloud(클로드/GPT, 선택). 둘 다 이미 있음.
# ═══════════════════════════════════════════════════════════════
import os
import json

import llm

# 역할별 두뇌 선호: 'local'(ollama) | 'cloud'(키 있으면 클라우드, 없으면 로컬)
#  반론·합성은 '머리'가 중요 → cloud 선호. 설계·구현은 로컬로 충분(비용 0).
ROLE_BRAIN = {
    "planner": "local",
    "implementer": "local",
    "reviser": "local",
    "critic": "cloud",
    "synth": "cloud",
}

# ── 역할 가감 설정(council_config.json) ─────────────────────────
#  {"max_rounds":2, "role_brain":{"critic":"local"}, "lenses":["보안","성능"]}
#  · max_rounds : 반론↔재구현 라운드 최대(깨끗하면 조기종료)
#  · role_brain : 역할별 두뇌 오버라이드(local/cloud)
#  · lenses     : 추가 적대적 검증 관점(렌즈). 메인 반론 + 관점별 전문 반론(보안/성능/엣지 등)
_CCFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "council_config.json")


def _load_ccfg():
    cfg = {"max_rounds": 2, "role_brain": {}, "lenses": []}
    if os.path.exists(_CCFG_FILE):
        try:
            with open(_CCFG_FILE, encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if k in cfg})
        except Exception:
            pass
    return cfg


_CCFG = _load_ccfg()
ROLE_BRAIN.update(_CCFG.get("role_brain") or {})
LENSES = list(_CCFG.get("lenses") or [])

LENS_SYS = ("너는 '{lens}' 관점의 전문 검증관이다. 위 [구현]을 오직 {lens} 측면에서만 적대적으로 "
            "검토해 결함을 콕 집어라(다른 관점은 무시). 결함 없으면 '치명 결함 없음'. 한국어, 짧게.")

PLANNER_SYS = (
    "너는 냉정한 설계자다. 주어진 작업을 분해하고 접근법·핵심 결정·주의할 엣지케이스를 "
    "짧게 정리해라. 여기선 코드 쓰지 말고 '계획'만(불릿 위주, 군더더기 0). 한국어.")
IMPLEMENTER_SYS = (
    "너는 구현자다. 위 [설계]대로 실제로 동작하는 코드/로직을 작성해라. 간결·정확하게, "
    "코드 위주로(설명은 최소). 설계에서 빠진 부분이 보이면 합리적으로 메워라.")
CRITIC_SYS = (
    "너는 적대적 반론·검증관이다. 임무는 위 [구현]을 의심하고 부수는 것 하나뿐 — 다시 쓰거나 "
    "칭찬하지 마라. 버그·엣지케이스·틀린 가정·경계조건·성능/보안 결함을 콕 집어 '왜 문제인지'와 "
    "'어떻게 깨지는지'를 구체적으로. 치명 결함 없으면 '치명 결함 없음'이라 솔직히. 한국어.")
REVISER_SYS = (
    "너는 구현자다. [반론]에서 지적된 결함을 실제로 고쳐 [구현]을 개선해라. 지적 안 된 부분은 "
    "그대로 유지(괜히 갈아엎지 마라). 고친 전체 코드를 코드 위주로 내라(설명 최소).")
SYNTH_SYS = (
    "너는 합성자다. [설계]·[최종 구현]·[마지막 반론]을 종합해 '최종 해답'을 내라. 남은 지적이 있으면 "
    "반영해 마무리하고, 바로 쓸 수 있는 최종 코드 + 왜 그렇게 했는지 2~3줄. 군더더기·중복 설명 0.")

# ── 일반(비코딩) 모드 역할 — 전략·판단·설계 같은 주제 회의용 ──
G_PLANNER_SYS = (
    "너는 분석가다. 주제를 분해하고 핵심 쟁점·고려할 변수·볼 관점들을 짧게 정리해라. "
    "여기선 결론 내지 말고 '분석 틀'만(불릿 위주, 군더더기 0). 한국어.")
G_IMPLEMENTER_SYS = (
    "너는 초안 작성자다. 위 [분석]을 바탕으로 주장/방안의 초안을 근거와 함께 구체적으로 내라. "
    "두루뭉술·양비론 금지, 입장을 분명히. 한국어.")
G_REVISER_SYS = (
    "너는 초안 보강자다. [반론]에서 지적된 약점을 실제로 반영해 주장/방안을 개선해라. "
    "지적 안 된 부분은 유지(괜히 갈아엎지 마라). 한국어.")
G_CRITIC_SYS = (
    "너는 적대적 반론자다. 위 [초안]을 의심하고 부수는 게 임무 — 다시 쓰거나 칭찬하지 마라. "
    "약점·반례·놓친 관점·과장된 단정·근거 빈 곳을 콕 집어 '왜 틀릴 수 있는지' 구체적으로. "
    "치명 결함 없으면 '치명 결함 없음'. 한국어, 짧고 매섭게.")
G_SYNTH_SYS = (
    "너는 합성자다. [분석]·[초안]·[반론]을 종합해 균형 잡힌 '최종 결론'을 내라. 반론에서 살아남은 "
    "근거만 쓰고, 결론 + 핵심 근거 2~3 + 확신도(확실/높음/추측) + 뒤집을 조건 한 줄. 군더더기 0. 한국어.")

ROLE_PROMPTS = {
    "code": {"planner": PLANNER_SYS, "implementer": IMPLEMENTER_SYS,
             "reviser": REVISER_SYS, "critic": CRITIC_SYS, "synth": SYNTH_SYS},
    "general": {"planner": G_PLANNER_SYS, "implementer": G_IMPLEMENTER_SYS,
                "reviser": G_REVISER_SYS, "critic": G_CRITIC_SYS, "synth": G_SYNTH_SYS},
}


def _cloud_keyed():
    """키가 실제로 있는 클라우드 프로바이더(active 토글과 무관). 없으면 None."""
    try:
        import cloud
        for name in ("claude", "gpt", "deepseek"):
            prov = cloud._provider(name)
            if prov and cloud._key(prov):
                return prov
    except Exception:
        pass
    return None


def brain_label(prefer):
    """이 역할이 실제로 어느 두뇌로 돌지 표시용."""
    if prefer == "cloud":
        prov = _cloud_keyed()
        if prov:
            return f"cloud:{prov['name']}({prov['model']})"
        if _llm_api_ready():
            return "api(llm)"
        return "local(폴백)"
    return "local"


def _llm_api_ready():
    try:
        return bool(llm._api_key(llm._CFG))
    except Exception:
        return False


def _gen(messages, prefer="local", temperature=0.3, num_predict=2048):
    """역할 호출 라우터. prefer='cloud'면 키 있는 클라우드→llm API→로컬 순 폴백."""
    if prefer == "cloud":
        prov = _cloud_keyed()
        if prov:
            try:
                import cloud
                r = cloud.chat(prov, messages, num_predict=num_predict,
                               temperature=temperature)
                return r["message"]["content"]
            except Exception:
                pass
        if _llm_api_ready():
            try:
                return llm.strip_think(
                    llm.chat(messages, backend="api", temperature=temperature))
            except Exception:
                pass
    # 로컬 폴백(ollama)
    return llm.strip_think(llm.chat(messages, backend="local", temperature=temperature))


def _role(name, sys_prompt, ctx, on_event, disp=None):
    prefer = ROLE_BRAIN.get(name, "local")
    if on_event:
        on_event("role", {"role": disp or name, "brain": brain_label(prefer)})
    out = _gen([{"role": "system", "content": sys_prompt},
                {"role": "user", "content": ctx}],
               prefer=prefer,
               temperature=0.1 if name in ("critic", "synth") else 0.3)
    return (out or "").strip()


def _is_clean(crit):
    """반론관이 '치명 결함 없음'으로 마무리했는지(조기 종료 판단)."""
    t = (crit or "").replace(" ", "")
    return ("치명결함없음" in t) or ("결함없음" in t) or ("문제없음" in t)


def council(task, root=None, on_event=None, max_rounds=None, mode="code"):
    """설계→구현→(반론↔재구현 라운드 반복)→합성 회의. 반환 {ok, result, transcript}.
       mode='code'(코딩) | 'general'(전략·판단 등 일반 주제) — 역할 프롬프트가 달라진다.
       max_rounds 미지정 시 council_config.json 값(기본 2). lenses 설정 시 관점별 전문 반론 추가."""
    if not (task or "").strip():
        return {"ok": False, "msg": "작업이 비었어."}
    if max_rounds is None:
        max_rounds = _CCFG.get("max_rounds", 2)
    max_rounds = max(1, int(max_rounds))
    P = ROLE_PROMPTS.get(mode, ROLE_PROMPTS["code"])
    base = f"[작업]\n{task}\n" + (f"\n[작업 폴더] {root}\n" if root else "")

    plan = _role("planner", P["planner"], base, on_event)
    impl = _role("implementer", P["implementer"],
                 base + "\n[설계]\n" + plan, on_event)

    rounds = []
    crit = ""
    for r in range(max_rounds):
        ctx = base + "\n[설계]\n" + plan + "\n\n[구현]\n" + impl
        # 메인 반론 + (설정 시) 관점별 전문 반론(보안·성능·엣지 등) — 적대적 다각 검증
        parts, all_clean = [], True
        main_crit = _role("critic", P["critic"], ctx, on_event, disp=f"critic(R{r + 1})")
        parts.append(main_crit)
        if not _is_clean(main_crit):
            all_clean = False
        for lens in LENSES:
            lc = _role("critic", LENS_SYS.format(lens=lens), ctx, on_event,
                       disp=f"{lens}(R{r + 1})")
            parts.append(f"[{lens} 관점]\n{lc}")
            if not _is_clean(lc):
                all_clean = False
        crit = "\n\n".join(parts)
        rounds.append({"round": r + 1, "critic": crit, "impl": impl})
        if all_clean:
            if on_event:
                on_event("clean", {"round": r + 1})
            break
        if r == max_rounds - 1:        # 마지막 라운드면 재구현 없이 합성으로
            break
        impl = _role("reviser", P["reviser"],
                     base + "\n[설계]\n" + plan + "\n\n[구현]\n" + impl
                     + "\n\n[반론]\n" + crit, on_event, disp=f"reviser(R{r + 1})")

    final = _role("synth", P["synth"],
                  base + "\n[설계]\n" + plan + "\n\n[최종 구현]\n" + impl
                  + "\n\n[마지막 반론]\n" + crit, on_event)

    if on_event:
        on_event("done", {"chars": len(final), "rounds": len(rounds)})
    return {"ok": True, "result": final,
            "transcript": {"plan": plan, "impl": impl, "critic": crit,
                           "final": final, "rounds": rounds}}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "파이썬으로 안전한 정수 나눗셈 함수(0 나눗셈·overflow 처리) 짜줘"

    def ev(kind, data):
        if kind == "role":
            print(f"  ◆ {data['role']}  [{data['brain']}] …")
        elif kind == "clean":
            print(f"  ✓ 라운드 {data['round']}: 치명 결함 없음 → 조기 종료")
        elif kind == "done":
            print(f"  ✔ 회의 완료 ({data['chars']}자 · {data.get('rounds', 1)}라운드)")

    print(f"[회의 시작] {q}\n")
    out = council(q, on_event=ev)
    print("\n" + "=" * 60)
    print(out.get("result") if out.get("ok") else out.get("msg"))
