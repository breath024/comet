# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 회의 — 클로드 + 아스트라(GPT) 두 머리가 따지고 결론을 낸다
#
#  흐름:  1) 각자 입장(동시)  →  2) 상대 답을 보고 반박·수정(동시)  →  3) 클로드가 결론 정리
#  두뇌:  API 키가 아니라 호윤 구독의 CLI 를 부른다.
#         · 클로드  : claude -p  (도구 끔, 세션 안 남김)
#         · 아스트라: codex exec -m gpt-6-astra --sandbox read-only
#         ★호윤 본인 비서 안에서만 쓴다 — 남의 요청을 구독으로 처리하는 건 약관 밖(메모리 참고).
#         ★ANTHROPIC_API_KEY 가 환경에 있으면 claude 가 API 과금으로 돈다 — 자식 환경에서 뺀다.
#  한쪽이 실패하면(한도·오류) 남은 쪽으로 진행, 둘 다 실패하면 로컬 회의(council.py)로.
#  시간: 대략 1~3분. 그래서 comet 은 뒤에서 돌리고 끝나면 배너+음성으로 알린다.
# ═══════════════════════════════════════════════════════════════
import os
import re
import json
import shutil
import tempfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_FILE = os.path.join(_DIR, "debate_last.json")
ASTRA_MODEL = "gpt-6-astra"
NAMES = {"claude": "클로드", "astra": "아스트라"}

OPINION_SYS = (
    "너는 호윤의 비서 COMET 의 회의에 불려 온 참가자다. 아래 질문에 네 입장을 분명히 내라.\n"
    "형식: 결론 한 줄 → 근거 2~3개(짧게) → 확신도(확실/높음/추측) → 입장이 뒤집힐 조건 한 줄.\n"
    "양비론·두루뭉술 금지. 모르는 사실은 지어내지 말고 '확인 필요'라고 써라. 한국어, 500자 이내.\n"
    "파일을 읽거나 쓰지 말고 답만 글로 줘.")
REBUT_SYS = (
    "같은 질문에 다른 참가자가 아래처럼 답했다. 그 답의 약점·빠진 점을 콕 집고, 맞는 부분은 인정해라.\n"
    "그다음 네 최종 입장을 다시 한 줄로(바뀌었으면 왜 바뀌었는지). 한국어, 400자 이내. 파일은 건드리지 마.")
SYNTH_SYS = (
    "너는 회의 정리자다. 두 참가자(클로드·아스트라)의 입장과 반박을 보고 최종 결론을 내라.\n"
    "형식:\n결론: (한두 줄)\n합의: (둘이 같은 점)\n갈린 점: (다른 점과 어느 쪽 근거가 더 센지)\n"
    "확신도: 확실/높음/추측\n뒤집을 조건: (한 줄)\n"
    "마지막 줄에 '말:' 로 시작해 소리로 읽어줄 요약 두 문장(반말, 숫자는 짧게).\n"
    "반박에서 살아남은 근거만 써라. 한국어. 파일은 건드리지 마.")


def _child_env():
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ask_claude(prompt, timeout=300, tools="", cwd=None):
    """tools="" 면 도구를 다 끈다(회의용). "WebSearch" 처럼 주면 그것만 열린다.
       ★--tools 로 열기만 하면 비대화형에선 권한이 거부돼 빈손으로 돌아온다(실측) —
         --allowed-tools 로 미리 허락까지 같이 줘야 실제로 검색한다.
       cwd=None 이면 임시폴더에서 돈다(회의용 — 프로젝트 CLAUDE.md 가 안 끌려오게).
       cwd=경로 를 주면 그 폴더에서 돈다(위임용 — 그 프로젝트의 CLAUDE.md·파일을 봐야 하니까)."""
    exe = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")
    args = [exe, "-p", "--no-session-persistence", "--output-format", "text", "--tools", tools]
    if tools:
        args += ["--allowed-tools", tools]
    with tempfile.TemporaryDirectory(prefix="comet_claude_") as tmp:
        r = subprocess.run(args,
                           input=prompt.encode("utf-8"), capture_output=True, cwd=(cwd or tmp),
                           env=_child_env(), timeout=timeout, creationflags=_flags())
    out = r.stdout.decode("utf-8", "ignore").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError("claude: " + (r.stderr.decode("utf-8", "ignore").strip()[-200:] or out[-200:]
                                         or f"exit {r.returncode}"))
    return out


def ask_astra(prompt, timeout=420, web=False):
    """web=True 면 웹 검색을 켠다(codex 는 플래그가 아니라 설정값으로 켠다)."""
    exe = shutil.which("codex") or os.path.expanduser(
        r"~\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe")
    with tempfile.TemporaryDirectory(prefix="comet_astra_") as cwd:
        out_file = os.path.join(cwd, "out.md")
        args = [exe, "exec", "-m", ASTRA_MODEL, "--sandbox", "read-only", "--skip-git-repo-check"]
        if web:
            args += ["-c", "tools.web_search=true"]
        r = subprocess.run(args + ["-o", out_file, "-"],
                           input=prompt.encode("utf-8"), capture_output=True, cwd=cwd,
                           env=_child_env(), timeout=timeout, creationflags=_flags())
        text = ""
        if os.path.exists(out_file):
            with open(out_file, encoding="utf-8", errors="ignore") as f:
                text = f.read().strip()
    if not text:
        log = (r.stdout + r.stderr).decode("utf-8", "ignore")
        lim = re.search(r"usage limit[^\n]*", log, re.I)          # "try again at 2:47 AM" 을 그대로 알린다
        raise RuntimeError("astra: " + (lim.group(0) if lim else log.strip()[-200:] or f"exit {r.returncode}"))
    return text


ASK = {"claude": ask_claude, "astra": ask_astra}


def _both(prompts, on_event):
    """{이름: 프롬프트} 를 동시에 → {이름: 답}. 실패한 쪽은 빠진다."""
    out = {}
    with ThreadPoolExecutor(len(prompts)) as ex:
        futs = {name: ex.submit(ASK[name], p) for name, p in prompts.items()}
        for name, fu in futs.items():
            try:
                out[name] = fu.result()
            except Exception as e:
                if on_event:
                    on_event("fail", {"who": NAMES[name], "err": str(e)[:160]})
    return out


def _local_fallback(topic, on_event):
    import council
    if on_event:
        on_event("stage", {"msg": "둘 다 안 돼서 로컬 회의로"})
    out = council.council(topic, mode="general")
    if not out.get("ok"):
        return {"ok": False, "msg": out.get("msg", "로컬 회의도 실패")}
    return {"ok": True, "result": out["result"], "speech": "", "who": ["로컬"],
            "opinions": {}, "rebuttals": {}}


def debate(topic, context="", on_event=None):
    """반환 {ok, result(정리 전문), speech(읽어줄 두 문장), who, opinions, rebuttals}"""
    topic = (topic or "").strip()
    if not topic:
        return {"ok": False, "msg": "주제가 비었어."}
    base = f"[질문]\n{topic}\n" + (f"\n[앞선 대화 — 참고만]\n{context}\n" if context else "")

    if on_event:
        on_event("stage", {"msg": "각자 입장"})
    ops = _both({n: OPINION_SYS + "\n\n" + base for n in ASK}, on_event)
    if not ops:
        return _local_fallback(topic, on_event)

    rebs = {}
    if len(ops) == 2:
        if on_event:
            on_event("stage", {"msg": "서로 반박"})
        other = {"claude": "astra", "astra": "claude"}
        rebs = _both({n: base + f"\n[네 답]\n{ops[n]}\n\n[{NAMES[other[n]]}의 답]\n{ops[other[n]]}\n\n" + REBUT_SYS
                      for n in ops}, on_event)

    if on_event:
        on_event("stage", {"msg": "결론 정리"})
    body = base
    for n in ops:
        body += f"\n[{NAMES[n]} 입장]\n{ops[n]}\n"
        if n in rebs:
            body += f"\n[{NAMES[n]} 반박·최종]\n{rebs[n]}\n"
    try:
        final = ask_claude(SYNTH_SYS + "\n\n" + body)
    except Exception as e:                     # 정리 담당이 막히면 아스트라가 정리
        if on_event:
            on_event("fail", {"who": "클로드(정리)", "err": str(e)[:160]})
        final = ask_astra(SYNTH_SYS + "\n\n" + body)

    m = re.search(r"^\s*말\s*[:：]\s*(.+)$", final, re.M | re.S)
    speech = m.group(1).strip() if m else ""
    result = final[:m.start()].rstrip() if m else final
    who = [NAMES[n] for n in ops]
    res = {"ok": True, "topic": topic, "result": result, "speech": speech, "who": who,
           "opinions": {NAMES[n]: v for n, v in ops.items()},
           "rebuttals": {NAMES[n]: v for n, v in rebs.items()},
           "at": datetime.now().isoformat(timespec="seconds")}
    try:
        with open(LAST_FILE, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    if on_event:
        on_event("done", {"who": who})
    return res


def last():
    try:
        with open(LAST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def format_full(res):
    """화면·기록용 전문."""
    if not res:
        return "아직 회의한 적 없어."
    lines = [f"🧠 회의 — {res.get('topic', '')}  ({' + '.join(res.get('who', []))})", "", res["result"]]
    for name, op in (res.get("opinions") or {}).items():
        lines += ["", f"── {name} 입장 ──", op]
        if name in (res.get("rebuttals") or {}):
            lines += [f"── {name} 반박 ──", res["rebuttals"][name]]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "개인 투자자가 금리 인상기에 현금 비중을 늘리는 게 맞아?"
    r = debate(q, on_event=lambda k, d: print(f"  · {k} {d}"))
    print(format_full(r) if r.get("ok") else r.get("msg"))
    print("\n말:", r.get("speech"))
