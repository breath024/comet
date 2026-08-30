# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 에이전트 루프 (ReAct 방식, JSON 프로토콜)
#
#  클로드 코드의 핵심을 본뜬 것: 모델이 "생각→도구 호출"을 반복하고,
#  매번 도구 결과를 다시 받아 다음 수를 둔다. 끝나면 done.
#
#  왜 네이티브 tool-calling 대신 JSON 프로토콜인가:
#   - 로컬 14b는 네이티브 tool-calling 이 불안정(comet.py 주석 참고).
#   - JSON 한 덩이를 뱉게 하면 로컬·API 양쪽에서 똑같이 안정적으로 파싱된다.
# ═══════════════════════════════════════════════════════════════
import json

import llm
import dev_tools

PROTOCOL = """너는 COMET-Coder, 호윤의 로컬 코딩 에이전트다. 다음 5단계를 거쳐 일을 끝낸다: 조사 → 계획 → 구현 → 검증 → 비판적 재검토.
근거 없이 넘겨짚지 않고, 매 단계를 앞 단계의 결과로 검증하며 나아가는 게 핵심이다(추측 주도가 아니라 증거 주도).

[5단계]
1) 조사(scout) — read_file/list_dir/grep 으로 관련 코드를 먼저 읽는다. 추측 금지. 방식을 정할 근거(기존 코드의 비슷한 사례, 컨벤션)를 여기서 확보한다. 같은 문제가 여러 파일에 있으면 grep 으로 전부 찾아 목록을 만든다.
2) 계획(plan) — 조사에서 찾은 근거를 바탕으로: 조건/제약을 번호로 나열, 고칠 곳을 전부 나열(1곳이면 1곳), 무엇이 되면 끝인지(완료 조건)를 한 줄로 정한다. 근거 없이 새 방식을 지어내지 않는다.
3) 구현(implement) — 계획대로 한 곳씩 edit_file/write_file. 여러 곳이면 첫 곳에서 검증된 방식을 나머지 전부에 그대로 적용한다. 계획과 실제가 어긋나면 조용히 즉흥적으로 바꾸지 말고 phase를 계획(plan)으로 되돌려 계획을 고친 뒤 다시 구현한다.
4) 검증(verify) — run_shell 로 정상 케이스와 경계/예외 케이스를 직접 돌려 결과값을 완료 조건과 비교한다. "에러 없이 돌아간다" ≠ "결과가 맞다". 고친 곳이 여러 파일이면 파일마다 각각 실행해서 확인한다(하나만 보고 나머지를 넘겨짚지 않는다).
   ★함수·클래스를 만들었으면 **파일을 그냥 실행하는 것으로는 검증이 아니다.** 정의만 있는 파일은 아무것도 출력하지 않고 exit 0 으로 끝나므로 뭘 확인한 게 아니다. 반드시 그 함수를 **직접 호출하고 반환값을 print 해서** 눈으로 보고, 기대값과 같은지 비교해라.
     ✗ python dedupe.py                                    (아무 출력도 없다. 통과가 아니다)
     ○ python -c "from dedupe import dedupe; print(dedupe([1,2,1,3]))"   → [1, 2, 3] 인지 눈으로 확인
   ★출력의 **값뿐 아니라 타입·형태**도 봐라. 리스트를 돌려줘야 하는데 `<generator object ...>` 나 `None` 이 찍혔으면 틀린 것이다(`return` 을 쓸 자리에 `yield` 를 쓰면 이렇게 된다).
5) 비판적 재검토(critique) — done 선언 전 마지막으로 한 번, 계획(2단계)의 조건 목록을 다시 꺼내 스스로에게 반문한다: 놓친 조건이 있는가? 파일마다 다르게 고친 곳은 없는가? 검증한 케이스가 조건을 다 커버하는가? 문제를 찾으면 구현(3단계)으로 돌아간다. 문제가 없다고 확인됐을 때만 done.

[행동 규칙]
- 매 턴 반드시 JSON 객체 하나만 출력한다. 설명 문장·코드펜스·인사 없이 순수 JSON 만.
- 형식: {"phase": "조사|계획|구현|검증|비판", "thought": "지금 무엇을/왜", "tool": "도구이름", "args": { ... }}
- tool 은 반드시 아래 도구 목록(또는 done/ask) 중 하나여야 한다. "계획"이나 "비판"은 도구가 아니라 phase 필드에만 적는 이름이다 — 계획/비판 단계에서도 thought에 생각을 적고 tool 은 실제 도구(예: grep/list_dir/read_file, 이미 다 봤으면 다음 실행할 edit_file/run_shell)를 골라라.
- thought 는 한두 문장으로 짧게. summary 도 장황하게 늘어놓지 마라.
- 간단한 일(파일 하나 읽기, 사소한 오타 수정 등)까지 5단계를 억지로 늘리지 마라 — 그럴 땐 조사→구현→검증만으로 끝내도 된다. 다중 조건·여러 파일·근본원인 추적처럼 근거와 일관성이 중요한 작업에서 이 5단계가 힘을 발휘한다.
- 에러가 나면 stderr 를 읽고 원인을 고친 뒤 다시 돌려라. 같은 시도를 반복하지 마라.
- 정보가 더 필요하거나 사람의 결정이 필요하면 {"tool":"ask","args":{"question":"물어볼 것"}} 으로 멈춰서 물어라.
- 일이 끝났으면 {"tool":"done","args":{"summary":"한 일 요약"}} 을 출력한다. 파일을 하나라도 고쳤다면 run_shell 로 검증하기 전엔 done 이 받아들여지지 않는다 — 검증 없이 부르면 한 번 되돌려 보낸다.

__TOOL_SPEC__

[작업 디렉터리] __ROOT__

지금부터 아래 작업을 수행해라. 첫 출력부터 JSON 이어야 한다. 조사(1단계)부터 시작해라."""


def _fmt_result(name, result):
    """도구 결과를 모델에게 돌려줄 압축 텍스트로."""
    if name == "read_file" and result.get("ok"):
        body = result["content"]
        tail = "  …(생략됨)" if result.get("truncated") else ""
        return f"[read_file {result['path']}]\n{body}{tail}"
    if name == "run_shell":
        return (f"[run_shell code={result.get('code')}]\n"
                f"STDOUT:\n{result.get('stdout','')}\n"
                f"STDERR:\n{result.get('stderr','')}")
    if name in ("write_file", "edit_file") and result.get("ok"):
        d = result.get("diff", "")
        return f"[{name}] {result.get('msg','')}" + (f"\n{d}" if d and d != "(새 파일)" else "")
    return f"[{name}] " + json.dumps(result, ensure_ascii=False)[:6000]


def run(task, box=None, skill_prompt="", max_steps=25, on_event=None,
        history=None, temperature=0.1, force_backend=None):
    """task 를 끝까지 수행. box=ToolBox(없으면 cwd). on_event(kind, data)로 진행 보고.
       force_backend='local'/'api' 로 백엔드 강제(자동 승급용). 없으면 설정대로.
       반환: {"ok","summary","steps","stopped","messages"}. ask/limit 면 messages 로 이어갈 수 있음."""
    box = box or dev_tools.ToolBox()
    import cost_guard
    cost_guard.reset_task()              # 과제별 API 호출 카운터 초기화
    system = PROTOCOL.replace("__TOOL_SPEC__", dev_tools.TOOL_SPEC).replace("__ROOT__", box.root)
    if skill_prompt:
        system += "\n\n[이 작업의 스킬 지침]\n" + skill_prompt
    messages = [{"role": "system", "content": system}]
    if history:
        messages += history
    messages.append({"role": "user", "content": task})
    return _loop(messages, box, max_steps, on_event, temperature, force_backend)


def resume(messages, reply, box, max_steps=25, on_event=None, temperature=0.1,
           force_backend=None):
    """ask/limit 로 멈춘 작업을 사용자 답변(reply)을 붙여 이어서 진행."""
    messages = list(messages) + [{"role": "user", "content": reply}]
    return _loop(messages, box, max_steps, on_event, temperature, force_backend)


# 사고방지 임계값 — 무한루프/헛돌기 차단
MAX_REPEAT = 3       # 같은 동작(도구+인자)을 이만큼 반복하면 중단
MAX_FAILS = 4        # 도구가 연속 이만큼 실패하면 중단


MAX_DONE_BOUNCE = 2   # 검증 없이 done 부르는 걸 되돌려보내는 최대 횟수(무한반송 방지)
MAX_PSEUDO_TOOL = 3   # plan/critique 를 도구처럼 부르는 걸 봐주는 최대 횟수(그 이상은 실패 처리로 넘김)


def _loop(messages, box, max_steps, on_event, temperature, force_backend=None):
    emit = on_event or (lambda *_: None)
    sigs = {}            # 동작 시그니처별 횟수(루프 감지)
    fails = 0            # 연속 도구 실패 수
    last_write_step = None   # 마지막으로 성공한 write_file/edit_file 걸음(검증 강제용)
    last_verify_step = None  # 그 이후 run_shell 을 돌린 걸음
    done_bounces = 0         # 검증 없이 done 불러서 되돌려보낸 횟수
    pseudo_tool_count = 0    # plan/critique 를 도구처럼 부른 횟수
    for step in range(1, max_steps + 1):
        try:
            raw = llm.chat(messages, temperature=temperature, stream=False,
                           backend=force_backend)
        except Exception as e:
            emit("error", str(e))
            return {"ok": False, "summary": f"중단(모델 호출 오류): {e}", "steps": step,
                    "stopped": "error", "messages": messages}
        action = llm.extract_json(raw)

        # JSON 을 못 뽑으면 = 모델이 그냥 말로 답함 → 그대로 전달(대화/질문)
        if not action or "tool" not in action:
            text = llm.strip_think(raw)
            emit("final", text)
            return {"ok": True, "summary": text, "steps": step, "stopped": "text"}

        tool = action.get("tool")
        args = action.get("args", {}) or {}
        thought = action.get("thought", "")
        emit("step", {"n": step, "thought": thought, "tool": tool, "args": args})

        if tool == "done":
            unverified = (last_write_step is not None and
                          (last_verify_step is None or last_verify_step < last_write_step))
            if unverified and done_bounces < MAX_DONE_BOUNCE:
                done_bounces += 1
                emit("done_bounced", {"n": step, "times": done_bounces})
                messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
                messages.append({"role": "user", "content":
                    "아직 검증(4단계) 없이 done을 불렀다 — 방금 고친 파일을 run_shell 로 직접 실행해 "
                    "결과값이 완료 조건과 맞는지 확인한 뒤에 다시 done을 불러라."})
                continue
            summary = args.get("summary") or args.get("message") or args.get("msg") or "완료"
            emit("done", summary)
            return {"ok": True, "summary": summary, "steps": step, "stopped": "done"}
        if tool == "ask":
            q = args.get("question", "추가 정보가 필요해.")
            emit("ask", q)
            return {"ok": True, "summary": q, "steps": step, "stopped": "ask",
                    "messages": messages}

        # "plan"/"critique" 는 도구가 아니라 phase 개념인데, 모델이 종종 이걸 도구처럼
        # 부른다(30B 로컬 모델의 습성 — 프롬프트로 완전히 못 없앰). 에러로 취급해 실패
        # 카운트를 소모하는 대신, 생각을 기록만 하고 다음 걸음에서 실제 도구를 고르게 한다.
        if tool in ("plan", "critique") and pseudo_tool_count < MAX_PSEUDO_TOOL:
            pseudo_tool_count += 1
            emit("note", {"n": step, "phase": tool, "args": args})
            messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
            messages.append({"role": "user", "content":
                f"[{tool}] 은 도구가 아니라 phase 필드에만 적는 이름이다. 방금 내용은 기록해뒀다 — "
                f"이제 실제 도구(read_file/grep/edit_file/write_file/run_shell/done/ask) 중 하나를 골라 계속해라."})
            continue

        # 무한루프 방지: 같은 동작(도구+인자) 반복 감지
        sig = tool + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
        sigs[sig] = sigs.get(sig, 0) + 1
        if sigs[sig] >= MAX_REPEAT:
            emit("loop", {"tool": tool, "times": sigs[sig]})
            return {"ok": False, "summary": f"같은 동작 {sigs[sig]}회 반복 — 무한루프 방지로 중단",
                    "steps": step, "stopped": "loop", "messages": messages}

        # 도구 실행
        result = dev_tools.call(box, tool, args)
        emit("result", {"tool": tool, "result": result})

        # 검증 강제용 추적: 수정했으면 last_write, 그 뒤 돌려봤으면 last_verify
        if tool in ("write_file", "edit_file") and result.get("ok"):
            last_write_step = step
        elif tool == "run_shell":
            last_verify_step = step

        # 헛돌기 방지: 연속 실패 누적 감지
        if result.get("ok", True):
            fails = 0
        else:
            fails += 1
            if fails >= MAX_FAILS:
                emit("stuck", fails)
                return {"ok": False, "summary": f"도구 {fails}회 연속 실패 — 중단",
                        "steps": step, "stopped": "stuck", "messages": messages}

        # 사용자가 확인에서 거부 → 모델에게 알리고 계속(다른 방법 찾게)
        messages.append({"role": "assistant", "content": json.dumps(action, ensure_ascii=False)})
        messages.append({"role": "user", "content": _fmt_result(tool, result)})

    emit("limit", max_steps)
    return {"ok": False, "summary": f"{max_steps}걸음 안에 못 끝냄", "steps": max_steps,
            "stopped": "limit", "messages": messages}


# 로컬이 막힌 것으로 판단하는 종료 사유들
_BAD = ("limit", "loop", "stuck", "error")


def run_smart(task, box=None, skill_prompt="", on_event=None, history=None):
    """자동 승급 오케스트레이터.
       - 설정 backend='local' → 로컬만.  'api' → API만.
       - 'auto'(기본) → 로컬 먼저 시도, 막히면(limit/loop/stuck/error) 키가 있고
         비용 가드가 허용할 때만 API 로 한 번 재시도(돈 사고방지)."""
    emit = on_event or (lambda *_: None)
    cfg = llm._CFG
    mode = cfg.get("backend", "auto")
    has_key = bool(llm._api_key(cfg))
    local_steps = cfg.get("max_steps_local", 18)
    api_steps = cfg.get("max_steps_api", 14)

    if mode == "api":
        return run(task, box=box, skill_prompt=skill_prompt, on_event=on_event,
                   history=history, max_steps=api_steps, force_backend="api")
    if mode == "local" or not has_key:
        return run(task, box=box, skill_prompt=skill_prompt, on_event=on_event,
                   history=history, max_steps=local_steps, force_backend="local")

    # auto + 키 있음 → 로컬 먼저(공짜), 막히면 API 승급
    res = run(task, box=box, skill_prompt=skill_prompt, on_event=on_event,
              history=history, max_steps=local_steps, force_backend="local")
    if res.get("stopped") not in _BAD or not cfg.get("auto_escalate", True):
        return res
    import cost_guard
    ok, why = cost_guard.before_api_call(0)
    if not ok:
        emit("guard", why)
        return res
    emit("escalate", res.get("stopped"))
    res2 = run(task, box=box, skill_prompt=skill_prompt, on_event=on_event,
               history=history, max_steps=api_steps, force_backend="api")
    res2["escalated"] = True
    return res2
