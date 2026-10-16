# -*- coding: utf-8 -*-
"""코멧의 손 — 정해진 도구 목록에 없는 「해 줘」를 클로드 코드(도구 켠 상태)에게 맡긴다 (2026-10-16).

왜: 코멧 도구는 시세 · 할일 · 파일 읽기 · 텔레그램 글 · 코드 위임 정도라, 목록 밖 일(로고 파일 보내기 ·
    화면 찍어 보내기 · 일정 지우기 · 올라마 켜기 · GPT 에게 묻기)이 오면 로컬 모델이 말로만 답하고
    「보냈어」 「삭제하겠습니다」를 지어냈다. 호윤: 「너가 할 수 있는 일의 대부분을 못 해」.

호윤이 정한 것(10/16):
  - PC 에서 온 말: 셸까지 켜고 위험 명령(삭제 · 프로세스 종료 · git reset/push · 포맷 · 환경변수 · 레지스트리)만 막는다.
  - 텔레그램에서 온 말: 읽기 · 찾기 · 웹 · 보내기만(10/10 T-18 — 계정이 털려도 PC 를 못 고치게).

파일 · 사진 · 화면을 텔레그램으로 보내는 건 클로드가 직접 하지 않고 답 끝에 표시만 남긴다 →
코멧(이 파일)이 비밀 파일인지 걸러서 보낸다. 그래서 텔레그램 쪽에서도 셸 없이 보낼 수 있다.
클로드가 막히면 아스트라(읽기 전용 · 웹)로.
"""
import os
import re
import shutil
import subprocess

import debate

HOME = os.path.expanduser("~")

FULL_TOOLS = "Read,Edit,Write,Glob,Grep,Bash,WebSearch,WebFetch"
READ_TOOLS = "Read,Glob,Grep,WebSearch,WebFetch"

#  막는 명령 — 「*X*」 는 명령 어디에 있든 걸린다(powershell -c "Remove-Item …" 안쪽까지 · 10/16 실측)
DENY = [
    "Bash(rm:*)", "Bash(rmdir:*)", "Bash(del:*)", "Bash(rd:*)", "Bash(erase:*)",
    "Bash(*Remove-Item*)", "Bash(*rm -r*)", "Bash(*rm -f*)", "Bash(*shutil.rmtree*)", "Bash(*os.remove*)",
    "Bash(*os.unlink*)", "Bash(*unlink(*)",
    "Bash(*Stop-Process*)", "Bash(*taskkill*)", "Bash(kill:*)", "Bash(pkill:*)", "Bash(*Stop-Service*)",
    "Bash(*git reset*)", "Bash(*git push*)", "Bash(*git clean*)", "Bash(*git checkout --*)",
    "Bash(*format *)", "Bash(*shutdown*)", "Bash(*Restart-Computer*)", "Bash(*diskpart*)",
    "Bash(*setx*)", "Bash(*reg add*)", "Bash(*reg delete*)", "Bash(*Set-ItemProperty*)",
    "Bash(*Unregister-ScheduledTask*)", "Bash(*Clear-Content*)",
]

#  밖으로 보내면 안 되는 것 — 키 · 토큰 · 비밀 파일
SECRET_DIRS = (os.path.join(HOME, "Desktop", "api"),)
SECRET_WORDS = ("token", "토큰", "secret", "apikey", "api_key", "password", "비번", "credential",
                ".env", "cf_token", "gmail_app", "daemon_token", "telegram_config", "numbers.json")

SEND_RE = re.compile(r"\[\[\s*보낼파일\s*:\s*(.+?)\s*\]\]")
SCREEN_RE = re.compile(r"\[\[\s*화면캡처\s*\]\]")

RULES = """너는 코멧(호윤의 상주 비서 AI)의 손이다. 호윤이 코멧에게 시킨 일을 **실제로 해라** — 방법 설명만 하고 끝내지 마라.
- 한 일만 「했다」고 말해라. 못 했거나 안 했으면 그대로 말해라. 지어내면 안 된다.
- 텔레그램(폰)으로 파일 · 사진을 보내야 하면 직접 보내지 말고 답 맨 끝에 한 줄씩 `[[보낼파일: 절대경로]]` 를 적어라 — 코멧이 보낸다.
  지금 PC 화면을 찍어 보내야 하면 `[[화면캡처]]` 한 줄. 이 표시는 실제로 있는 파일에만 써라.
- 파일 위치는 홈 폴더의 CLAUDE.md · 메모리 · 볼트 「환경 지도」를 먼저 보고 찾아라.
- 삭제 · 프로세스 종료 · 덮어쓰기 · 발송(메일 등) · 결제 · 키/환경변수 · 설정 바꾸기는 하지 말고, 무엇을 왜 해야 하는지만 말해라(「호윤 확인 필요」).
  막힌 명령을 다른 방법으로 돌아서 하지 마라.
- 키 · 토큰 · 비밀번호 파일(Desktop\\api 등)은 내용을 읽거나 보내지 마라.
- 코멧 자기 코드(Desktop\\개인프젝\\A.B.B.Y.S\\COMET\\ai)를 고쳐 달라는 일은 여기서 고치지 말고 「업그레이드 요청 <내용>」이라고
  코멧에게 말하라고 답해라 — 떼어 둔 복사본에서 고치고 검토 · 시험한 뒤 반영하는 길이 따로 있다.
- 답은 한국어로 짧게(5줄 이내) — 코멧이 그대로 말하고 소리로 읽을 수 있다. 무엇을 했고 결과가 뭔지만. 칭찬 · 군더더기 금지.
  마크다운(굵게 · 목록 기호 · 백틱 · 제목) 쓰지 마라 — 보통 문장으로. 경로는 그대로 적어도 된다."""

REMOTE_NOTE = ("\n- 지금은 텔레그램에서 온 말이라 읽기 · 찾기 · 웹만 열려 있다(셸 · 파일 고치기 없음). "
               "그게 필요한 일이면 「PC 앞(대화창 · 폰 웹)에서 시켜 줘」라고 답해라.")


def _secret(path):
    p = os.path.normcase(os.path.abspath(path))
    if any(p.startswith(os.path.normcase(d)) for d in SECRET_DIRS):
        return True
    low = os.path.basename(p).lower()
    return any(w in low for w in SECRET_WORDS)


def _ask_claude(prompt, system, tools, deny, timeout, cwd=HOME):
    exe = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")
    #  cwd = 홈 → 호윤 CLAUDE.md · 메모리가 그대로 붙는다(규칙 · 파일 위치를 클로드가 안다).
    #  위임(comet._delegate)은 고칠 프로젝트 폴더 — 그 폴더 CLAUDE.md · HANDOFF 를 보게
    args = [exe, "-p", "--strict-mcp-config", "--no-session-persistence", "--output-format", "text",
            "--permission-mode", "dontAsk", "--append-system-prompt", system,
            "--tools", tools, "--allowed-tools", tools]
    if deny:
        args += ["--disallowed-tools", *deny]
    r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, cwd=cwd,
                       env=debate._child_env(), timeout=timeout, creationflags=debate._flags())
    out = r.stdout.decode("utf-8", "ignore").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError("claude: " + (r.stderr.decode("utf-8", "ignore").strip()[-200:]
                                         or out[-200:] or f"exit {r.returncode}"))
    return out


def deliver(out):
    """답 끝의 [[보낼파일]] · [[화면캡처]] 를 실제로 보낸다 → (깨끗한 답, 보낸 결과 줄들)."""
    import notify
    notes = []
    paths = SEND_RE.findall(out)
    shot = bool(SCREEN_RE.search(out))
    clean = SCREEN_RE.sub("", SEND_RE.sub("", out)).strip()
    if (paths or shot) and not notify.configured():
        return clean, ["⚠️ 텔레그램 설정이 없어 못 보냈어."]
    if shot:
        try:
            import vision
            paths.insert(0, vision.capture_screen())
        except Exception as e:
            notes.append(f"⚠️ 화면 캡처 실패: {str(e)[:100]}")
    for p in paths[:10]:
        p = p.strip().strip('"').strip("'").strip("`")
        if _secret(p):
            notes.append(f"⛔ 비밀 파일이라 안 보냄: {os.path.basename(p)}")
            continue
        r = notify.send_file(p)
        notes.append(("📎 " if r.get("ok") else "⚠️ ") + r.get("msg", ""))
    return clean, notes


def run(task, context="", remote=False, timeout=600):
    """{ok, text, who} — text 는 코멧이 그대로 내보낼 글(보낸 파일 결과 줄 포함)."""
    system = RULES + (REMOTE_NOTE if remote else "")
    prompt = ((f"[최근 대화 — 참고만]\n{context}\n\n" if context else "")
              + f"[호윤이 코멧에게 시킨 일]\n{task}")
    out, who, err = "", "", []
    try:
        out = _ask_claude(prompt, system, READ_TOOLS if remote else FULL_TOOLS,
                          [] if remote else DENY, timeout)
        who = "클로드"
    except Exception as e:
        err.append(f"클로드: {str(e)[:120]}")
        print(f"  [맡기기] 클로드 실패 → 아스트라: {str(e)[:120]}")
        try:
            out = debate.ask_astra(system + "\n- 지금 너는 파일 읽기 · 웹만 된다(쓰기 · 명령 실행 안 됨).\n\n"
                                   + prompt, timeout=420, web=True)
            who = "아스트라"
        except Exception as e2:
            err.append(f"아스트라: {str(e2)[:120]}")
    if not out:
        return {"ok": False, "msg": "클로드 · 아스트라 둘 다 못 불렀어 — " + " / ".join(err)}
    clean, notes = deliver(out)
    text = clean + ("\n\n" + "\n".join(notes) if notes else "")
    return {"ok": True, "text": text, "who": who}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    remote = "--remote" in sys.argv
    task = " ".join(a for a in sys.argv[1:] if a != "--remote") or "바탕화면에 폴더가 몇 개인지 세어 줘"
    print(run(task, remote=remote))
