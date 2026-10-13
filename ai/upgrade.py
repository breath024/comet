# -*- coding: utf-8 -*-
"""COMET 자가 업그레이드 (2026-10-13 · 호윤 지시)

흐름(호윤이 고른 것): 코멧이 고칠 거리를 찾아 「제안」 → 호윤이 「업그레이드 1」 → 따로 떼어 둔 복사본(git worktree)에서
클로드가 고치고 아스트라가 검토 → 시험 → 호윤이 「업그레이드 적용」 해야 진짜 코드에 들어간다. 데몬 재시작은 호윤이.

막혔을 때 우회(호윤 「여러 케이스를 생각해야 해」):
  제안  : 클로드 → 아스트라 → 둘 다 안 되면 열린 이슈 · 반복 오류를 그대로 제안(모델 없이)
  고치기: 클로드 → 아스트라 → 둘 다 안 되면 「기다림」 — 30분마다 다시(최대 12번), 그래도 안 되면 실패로 알림
  검토  : 고친 쪽의 상대 → 상대도 안 되면 고친 쪽이 새 세션으로 자기 검토 → 그것도 안 되면 「검토 못 함」 표시하고 넘김
  지적 · 시험 실패: 고친 쪽이 한 번 더 고침(안 되면 상대가) → 다시 시험 · 검토. 그래도 시험 실패면 반영 못 하게 실패 처리
  적용  : 그 파일에 커밋 안 된 다른 수정이 있으면 거절 · 충돌이면 되돌리고 「업그레이드 다시」 안내
  중간에 꺼짐(데몬 재시작 등): 다음 점검 때 이어서 다시 시도
  떼어 둔 폴더 지우기: 안에 링크(정션)가 있으면 안 지움(메모리 feedback_junction_before_delete)

안전: 클로드는 --restricted(작업 폴더 밖 파일 못 건드림 · 명령 실행 도구 없음), 아스트라는 workspace-write 샌드박스.
      시작 · 적용 · 되돌리기는 텔레그램에서 온 말로는 안 함(remote_guard). 커밋에 Co-Authored-By 안 붙임(호윤 규칙).
명령: 업그레이드 [상태|제안|1~3|요청 <내용>|보기|적용|버려|다시|되돌리기]
"""
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter

_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(_DIR)                     # git 저장소 뿌리 = COMET
SUB = os.path.basename(_DIR)                     # "ai"
WORK = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), "comet_upgrade")
WT = os.path.join(WORK, "wt")
STATE = os.path.join(WORK, "state.json")

NAMES = {"claude": "클로드", "astra": "아스트라"}
ORDER = ["claude", "astra"]                       # 고치는 쪽 우선순위
EDIT_TIMEOUT = 1500
REVIEW_TIMEOUT = 480
RETRY_MIN = 30
RETRY_MAX = 12
TRANSIENT = re.compile(r"WinError 10060|10054|timed? ?out|getaddrinfo|Temporary failure|연결된 구성원", re.I)

_lock = threading.Lock()
_worker = None


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ── 상태 파일 ─────────────────────────────────────────
def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"job": None, "proposals": None, "history": [], "last_applied": None}


def _save(st):
    os.makedirs(WORK, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def _set_job(**kw):
    with _lock:
        st = _load()
        if st.get("job"):
            st["job"].update(kw)
            _save(st)
        return st.get("job")


def _history(st, title, status, commit=None):
    st.setdefault("history", []).append({"at": _now(), "title": title, "status": status, "commit": commit})
    st["history"] = st["history"][-30:]


# ── git ───────────────────────────────────────────────
def _git(*args, cwd=None):
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd or REPO, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", creationflags=_flags())
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _links_inside(root):
    """폴더 안의 링크 · 정션(지우면 원본까지 날 수 있음 — 10/09 사고)."""
    bad = []
    for d, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            p = os.path.join(d, name)
            if os.path.islink(p) or (hasattr(os.path, "isjunction") and os.path.isjunction(p)):
                bad.append(p)
        dirs[:] = [x for x in dirs if not os.path.islink(os.path.join(d, x))
                   and not (hasattr(os.path, "isjunction") and os.path.isjunction(os.path.join(d, x)))]
    return bad


def _cleanup_wt(branch=None):
    """떼어 둔 복사본과 가지를 치운다. 링크가 있으면 안 지우고 예외."""
    if os.path.exists(WT):
        bad = _links_inside(WT)
        if bad:
            raise RuntimeError(f"떼어 둔 폴더 안에 링크(정션)가 있어 안 지움 — 손으로 확인: {bad[:3]}")
        _git("worktree", "remove", "--force", WT)
        if os.path.exists(WT) and os.path.abspath(WT).startswith(os.path.abspath(WORK)):
            shutil.rmtree(WT, ignore_errors=True)          # git 이 모르는 남은 폴더(위에서 링크 없음 확인함)
    _git("worktree", "prune")
    if branch:
        _git("branch", "-D", branch)


def _prepare_wt(job):
    _cleanup_wt(job.get("branch"))
    os.makedirs(WORK, exist_ok=True)
    branch = "upgrade/" + dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    code, out = _git("worktree", "add", "-b", branch, WT, "HEAD")
    if code:
        raise RuntimeError("복사본을 못 만듦: " + out.strip()[-200:])
    base = _git("rev-parse", "HEAD", cwd=WT)[1].strip()
    _set_job(branch=branch, base=base)
    return os.path.join(WT, SUB)


def _changed():
    _git("add", "-A", cwd=WT)
    return [p for p in _git("diff", "--cached", "--name-only", cwd=WT)[1].splitlines() if p.strip()]


# ── 클로드 · 아스트라 ────────────────────────────────
def _claude_edit(prompt, cwd):
    import debate
    exe = shutil.which("claude") or os.path.expanduser(r"~\.local\bin\claude.exe")
    tools = "Read,Edit,Write,Glob,Grep"
    args = [exe, "-p", "--restricted", "--strict-mcp-config", "--no-session-persistence", "--output-format", "text",
            "--permission-mode", "acceptEdits", "--tools", tools, "--allowed-tools", tools]
    r = subprocess.run(args, input=prompt.encode("utf-8"), capture_output=True, cwd=cwd,
                       env=debate._child_env(), timeout=EDIT_TIMEOUT, creationflags=_flags())
    out = r.stdout.decode("utf-8", "ignore").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError((r.stderr.decode("utf-8", "ignore").strip() or out or f"exit {r.returncode}")[-200:])
    return out


def _astra_edit(prompt, cwd):
    import debate
    exe = shutil.which("codex") or os.path.expanduser(r"~\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe")
    with tempfile.TemporaryDirectory(prefix="comet_upg_") as tmp:
        out_file = os.path.join(tmp, "out.md")
        r = subprocess.run([exe, "exec", "-m", debate.ASTRA_MODEL, "--sandbox", "workspace-write",
                            "--skip-git-repo-check", "-C", cwd, "-o", out_file, "-"],
                           input=prompt.encode("utf-8"), capture_output=True, env=debate._child_env(),
                           timeout=EDIT_TIMEOUT, creationflags=_flags())
        text = open(out_file, encoding="utf-8", errors="ignore").read().strip() if os.path.exists(out_file) else ""
    if not text:
        log = (r.stdout + r.stderr).decode("utf-8", "ignore")
        lim = re.search(r"usage limit[^\n]*", log, re.I)
        raise RuntimeError(lim.group(0) if lim else (log.strip()[-200:] or f"exit {r.returncode}"))
    return text


def _edit(who, prompt, cwd):
    return (_claude_edit if who == "claude" else _astra_edit)(prompt, cwd)


def _ask(who, prompt):
    """글로만 답하기(도구 없음, 임시 폴더 — 고친 세션과 따로)."""
    import debate
    if who == "claude":
        return debate.ask_claude(prompt, timeout=REVIEW_TIMEOUT)
    return debate.ask_astra(prompt, timeout=REVIEW_TIMEOUT)


def _first_ok(order, fn):
    """order 차례로 fn(who) — 처음 성공한 (who, 결과, 실패목록). 다 실패면 (None, None, 실패목록)."""
    errs = []
    for who in order:
        try:
            return who, fn(who), errs
        except Exception as e:
            errs.append(f"{NAMES[who]}: {str(e).strip()[:160]}")
    return None, None, errs


# ── 제안 ─────────────────────────────────────────────
def _material():
    issue_lines = []
    try:
        md = open(os.path.join(_DIR, "ISSUES.md"), encoding="utf-8").read()
        sec = re.search(r"## 열린 것.*?\n(.*?)(?=\n## |\Z)", md, re.S)
        issue_lines = [l.strip("- ").strip() for l in (sec.group(1) if sec else "").splitlines()
                       if l.strip().startswith("-") and "없음" not in l]
    except Exception:
        pass
    errs = Counter()
    sample = {}
    cutoff = time.time() - 3 * 86400
    for p in glob.glob(os.path.join(_DIR, "logs", "daemon-*.log")):
        if os.path.getmtime(p) < cutoff:
            continue
        for line in open(p, encoding="utf-8", errors="replace"):
            if re.search(r"오류|실패|Traceback|Error|Exception", line):
                key = re.sub(r"\d+", "#", line[9:].strip())[:140]
                errs[key] += 1
                sample.setdefault(key, line.strip()[:200])
    error_top = [(n, sample[k]) for k, n in errs.most_common(12)]
    try:
        handoff = "".join(open(os.path.join(_DIR, "HANDOFF.md"), encoding="utf-8").readlines()[:70])
    except Exception:
        handoff = ""
    st = _load()
    done = [f"{h['title']} ({h['status']})" for h in st.get("history", [])[-10:]]
    return {"issue_lines": issue_lines, "error_top": error_top, "handoff": handoff, "done": done}


PROPOSE_SYS = """너는 COMET(호윤의 상주 비서 AI)의 관리자다. 아래 자료를 보고 코멧 코드를 고쳐 나아질 거리를 최대 3개 골라라.
고르는 순서: 호윤이 실제로 겪는 문제(열린 이슈 · 반복 오류) 먼저, 그다음 HANDOFF 의 남은 일 중 코드만으로 끝나는 것.
빼는 것: 일시적인 네트워크 끊김, 호윤 손(폰 · 계정 · 결제 · 재시작 · 실물)이 있어야 하는 일, 모델 · 페르소나 바꾸기,
아래 「지난 업그레이드」에 이미 있는 것, 30분 안에 못 끝낼 큰 일.
JSON 배열만 출력(설명 · 코드블록 없이):
[{"title": "짧은 제목(20자 이내)", "why": "왜 필요한지 한 줄 — 근거 자료", "request": "고칠 사람에게 줄 구체 지시(파일 · 증상 · 바라는 동작)"}]
고칠 게 없으면 []"""


def _parse_props(text):
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("제안 JSON 없음")
    arr = json.loads(m.group(0))
    out = [{"title": str(a["title"])[:30], "why": str(a.get("why", ""))[:200], "request": str(a["request"])}
           for a in arr if isinstance(a, dict) and a.get("title") and a.get("request")]
    return out[:3]


def _rule_props(mat):
    """모델 둘 다 못 쓸 때 — 열린 이슈 · 반복 오류(일시적 끊김 빼고)를 그대로."""
    props = [{"title": f"이슈 고치기: {l[:16]}", "why": "이슈 접수함에 열려 있음",
              "request": f"이슈 접수함(ISSUES.md)에 열린 이 이슈를 원인부터 찾아 고쳐라: {l}"} for l in mat["issue_lines"][:3]]
    for n, ex in mat["error_top"]:
        if len(props) >= 3:
            break
        if n >= 3 and not TRANSIENT.search(ex):
            props.append({"title": f"반복 오류 {n}번", "why": f"최근 사흘 데몬 기록에 {n}번",
                          "request": f"데몬 기록에 반복되는 이 오류를 원인부터 찾아 고쳐라: {ex}"})
    return props


def propose(push=True, log=print, force=False):
    """고칠 거리 찾기 → 상태에 저장 → (push) 배너 + 텔레그램. 자료가 지난번과 같으면 조용히(force 면 그래도)."""
    mat = _material()
    fp = hashlib.sha1(json.dumps([mat["issue_lines"], [e for _, e in mat["error_top"]], mat["handoff"]],
                                 ensure_ascii=False).encode()).hexdigest()[:12]
    st = _load()
    if not force and st.get("proposal_fp") == fp:
        log("[upgrade] 자료가 지난 제안 때와 같음 — 제안 안 함")
        return None
    body = (f"[열린 이슈]\n" + ("\n".join(mat["issue_lines"]) or "없음")
            + "\n\n[최근 사흘 데몬 오류 — 횟수 · 예시]\n" + ("\n".join(f"{n}번 · {e}" for n, e in mat["error_top"]) or "없음")
            + "\n\n[HANDOFF 앞부분]\n" + mat["handoff"]
            + "\n\n[지난 업그레이드]\n" + ("\n".join(mat["done"]) or "없음"))
    who, items, errs = _first_ok(ORDER, lambda w: _parse_props(_ask(w, PROPOSE_SYS + "\n\n" + body)))
    by = NAMES.get(who)
    if who is None:
        items, by = _rule_props(mat), "규칙(클로드 · 아스트라 둘 다 못 씀)"
        log("[upgrade] 제안 — 모델 둘 다 실패: " + " / ".join(errs))
    with _lock:
        st = _load()
        st["proposals"] = {"at": _now(), "by": by, "items": items}
        st["proposal_fp"] = fp
        _save(st)
    if not items:
        log(f"[upgrade] 제안할 거 없음({by})")
        return "지금은 고칠 거리 못 찾았어."
    text = (f"코멧 업그레이드 제안 ({by})\n"
            + "\n".join(f"{i}) {p['title']} — {p['why']}" for i, p in enumerate(items, 1))
            + "\nPC 앞(콘솔 · 폰 웹)에서 「업그레이드 1」처럼 번호로 시작 · 「업그레이드 보기」는 어디서나")
    if push:
        _push("업그레이드 제안 " + str(len(items)) + "개", text)
    return text


# ── 고치기 ───────────────────────────────────────────
EDIT_SYS = """너는 COMET(호윤의 상주 비서 AI) 코드를 고치는 개발자다. 지금 폴더는 COMET/ai 를 따로 떼어 둔 복사본(git worktree)이다 — 여기 안에서만 고쳐라.
규칙
- 먼저 CLAUDE.md · 작업 유의사항.md 에서 관련 부분, 고칠 파일을 읽고 나서 고쳐라.
- 요청에 필요한 만큼만. 주변 코드 말투(한국어 주석 밀도 · 이름 · 짜임)를 따른다.
- 건드리지 말 것: HANDOFF.md · ISSUES.md · TODO.md · logs/ · profile/ · 키 · 토큰 · *_config.json, 페르소나 끝의 민감 개인사 가드레일.
- 패키지 설치 · 프로세스 종료 · 데몬 재시작 · 네트워크 발송 금지. 고칠 수 없거나 고칠 필요가 없으면 아무것도 바꾸지 말고 이유만 써라.
- 끝나면 맨 끝에 이 모양으로:
요약: (한 줄)
바꾼 것: (파일별 한 줄)
확인 방법: (호윤이 해 볼 것 한두 줄)
못 한 것: (없으면 없음)"""

REVIEW_SYS = """너는 코드 검토자다. 아래 요청으로 COMET(파이썬 상주 비서) 코드를 고친 diff 를 검토해라.
볼 것만: 실제로 터질 버그 · 요청과 다르게 동작 · 기존 기능 깨짐 · 보안(비밀 노출 · 외부 발송 · 파괴적 명령). 취향 · 말투 지적 금지.
첫 줄은 반드시 「판정: 통과」 또는 「판정: 고칠 것 있음」.
고칠 것 있으면 아래에 「파일:줄 — 무엇이 왜 문제 — 어떻게」 로 짧게. 파일은 건드리지 말고 글로만."""


def _summary(out):
    m = re.search(r"요약\s*[:：].*", out, re.S)
    return (m.group(0) if m else out[-600:]).strip()[:1200]


def _tests(changed):
    """바뀐 파이썬은 문법(py_compile) · pyflakes 가 있으면 정의 안 된 이름, JSON 은 읽히는지."""
    msgs, ok = [], True
    for p in changed:
        full = os.path.join(WT, p)
        if not os.path.exists(full):
            continue
        if p.endswith(".py"):
            r = subprocess.run([sys.executable, "-m", "py_compile", full], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", creationflags=_flags())
            if r.returncode:
                ok = False
                msgs.append(f"{p}: 문법 오류 {r.stderr.strip()[-300:]}")
                continue
            try:
                import pyflakes  # noqa: F401  — 없으면 건너뜀
                r = subprocess.run([sys.executable, "-m", "pyflakes", full], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", creationflags=_flags())
                bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
                if bad:
                    ok = False
                    msgs.append(f"{p}: " + "; ".join(bad[:5]))
            except ImportError:
                pass
        elif p.endswith(".json"):
            try:
                json.load(open(full, encoding="utf-8"))
            except Exception as e:
                ok = False
                msgs.append(f"{p}: JSON 오류 {e}")
    return ok, ("\n".join(msgs) if msgs else f"문법 · JSON 확인 통과 ({len(changed)}개 파일)")


def _review(job, editor):
    diff = _git("diff", "--cached", cwd=WT)[1]
    if len(diff) > 60000:
        diff = diff[:60000] + "\n…(길어서 잘림)"
    prompt = f"{REVIEW_SYS}\n\n[요청]\n{job['request']}\n\n[diff]\n{diff}"
    other = "astra" if editor == "claude" else "claude"
    errs = []
    for who, label in ((other, NAMES[other]), (editor, NAMES[editor] + "(새 세션 자기 검토)")):
        _set_job(step=f"검토 중 — {label}")
        try:
            text = _ask(who, prompt)
        except Exception as e:
            errs.append(f"{label}: {str(e).strip()[:120]}")
            continue
        m = re.search(r"판정\s*[:：]\s*(통과|고칠 것 있음)", text)
        return {"by": label, "ok": (m.group(1) == "통과") if m else None, "text": text.strip()[:3000]}
    return {"by": None, "ok": None, "text": "검토 못 함 — " + " / ".join(errs)}


def _run_job(log=print):
    st = _load()
    job = st.get("job") or {}
    title = job.get("title", "?")
    try:
        _set_job(step="복사본 만드는 중")
        cwd = _prepare_wt(job)
        _set_job(step="고치는 중")
        who, out, errs = _first_ok(ORDER, lambda w: (_set_job(step=f"고치는 중 — {NAMES[w]}"),
                                                     _edit(w, f"{EDIT_SYS}\n\n[요청]\n{job['request']}", cwd))[1])
        if who is None:
            return _to_waiting(errs, log)
        notes = [] + ([f"먼저 못 씀 — {e}" for e in errs])
        _set_job(editor=NAMES[who], summary=_summary(out), notes=notes)
        changed = _changed()
        if not changed:
            _finish("nochange", f"안 고침 — {NAMES[who]}: {_summary(out)[:300]}", log)
            return
        tests_ok, test_msg, review = False, "", {}
        for rnd in range(2):                                   # 처음 + 지적 고치기 한 번
            _set_job(step="시험 중")
            tests_ok, test_msg = _tests(changed)
            review = _review(_load()["job"], who)
            problems = []
            if not tests_ok:
                problems.append("시험 실패:\n" + test_msg)
            if review["ok"] is False:
                problems.append(f"{review['by']} 검토 지적:\n{review['text']}")
            if not problems or rnd == 1:
                break
            fix = (f"{EDIT_SYS}\n\n[원래 요청]\n{job['request']}\n\n[방금 고친 것에 대한 지적 — 이것만 고쳐라. "
                   f"지적이 틀렸다고 보면 고치지 말고 왜 틀렸는지 써라]\n" + "\n\n".join(problems))
            fixer_order = [who] + [w for w in ORDER if w != who]
            fw, fout, ferrs = _first_ok(fixer_order, lambda w: (_set_job(step=f"지적 고치는 중 — {NAMES[w]}"),
                                                                _edit(w, fix, cwd))[1])
            if fw is None:
                notes.append("지적 고치기 못 함 — " + " / ".join(ferrs))
                break
            notes.append(f"{NAMES[fw]}가 지적 고침: {_summary(fout)[:200]}")
            _set_job(notes=notes, summary=_summary(fout) if fw == who else _load()["job"].get("summary"))
            changed = _changed()
        _set_job(tests=test_msg, tests_ok=tests_ok, review=review, notes=notes,
                 files=changed, stat=_git("diff", "--cached", "--stat", cwd=WT)[1].strip()[-1500:])
        if not tests_ok:
            _finish("failed", "시험을 못 넘겨서 반영 안 함:\n" + test_msg, log, keep_wt=True)
            return
        j = _load()["job"]
        msg = f"코멧 업그레이드 — {title}\n\n{j.get('summary', '')[:1500]}\n\n검토: {review.get('by') or '못 함'}"
        code, out = _git("commit", "-q", "-m", msg, cwd=WT)
        if code:
            _finish("failed", "복사본 커밋 실패: " + out.strip()[-200:], log, keep_wt=True)
            return
        _set_job(status="ready", step="적용 기다림", commit=_git("rev-parse", "HEAD", cwd=WT)[1].strip(),
                 finished=_now())
        rv = review.get("by") or "못 함"
        verdict = {True: "통과", False: "지적 남음", None: "판정 불명"}[review.get("ok")]
        _push(f"업그레이드 준비됨: {title}",
              f"{j.get('summary', '')[:500]}\n고친 쪽 {j.get('editor')} · 검토 {rv}({verdict}) · {test_msg[:80]}\n"
              "PC 앞(콘솔 · 폰 웹)에서 「업그레이드 적용」 · 「업그레이드 보기」 · 「업그레이드 버려」")
    except Exception as e:
        log(f"[upgrade] 작업 오류: {e}")
        _finish("failed", f"작업 중 오류: {e}", log, keep_wt=True)


def _to_waiting(errs, log):
    st = _load()
    job = st["job"]
    n = int(job.get("attempts", 0)) + 1
    try:
        _cleanup_wt(job.get("branch"))
    except Exception as e:
        log(f"[upgrade] 복사본 정리 실패: {e}")
    why = " / ".join(errs)
    if n >= RETRY_MAX:
        _finish("failed", f"클로드 · 아스트라 둘 다 {n}번 연속 못 씀 — {why}", log)
        return
    retry = (dt.datetime.now() + dt.timedelta(minutes=RETRY_MIN)).strftime("%Y-%m-%d %H:%M")
    _set_job(status="waiting", attempts=n, retry_at=retry, step="기다림", error=why, branch=None)
    log(f"[upgrade] 둘 다 못 씀 — {retry} 에 다시 ({n}/{RETRY_MAX}): {why}")
    if n == 1:
        _push(f"업그레이드 잠깐 멈춤: {job['title']}",
              f"클로드 · 아스트라 둘 다 지금 못 씀({why[:150]}) — {RETRY_MIN}분마다 다시 해 볼게. 그만: 「업그레이드 버려」")


def _finish(status, why, log, keep_wt=False):
    with _lock:
        st = _load()
        job = st.get("job") or {}
        job.update(status=status, step="끝", error=why if status != "ready" else None, finished=_now())
        if status in ("nochange",):
            _history(st, job.get("title", "?"), "안 고침")
            st["job"] = None
        else:
            st["job"] = job
        _save(st)
    if not keep_wt:
        try:
            _cleanup_wt(job.get("branch"))
        except Exception as e:
            log(f"[upgrade] 복사본 정리 실패: {e}")
    log(f"[upgrade] {status}: {why[:200]}")
    label = {"nochange": "안 고침", "failed": "실패"}.get(status, status)
    _push(f"업그레이드 {label}: {job.get('title', '?')}",
          why[:600] + ("\n「업그레이드 보기」로 내용 · 「업그레이드 다시」 · 「업그레이드 버려」" if status == "failed" else ""))


def _alive(pid):
    if not pid:
        return False
    if pid == os.getpid():
        return bool(_worker and _worker.is_alive())
    out = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"], capture_output=True,
                         text=True, encoding="cp949", errors="replace", creationflags=_flags()).stdout
    return f'"{pid}"' in out


def _launch(log=print):
    global _worker
    _set_job(status="running", pid=os.getpid(), started=_now(), step="시작")
    _worker = threading.Thread(target=_run_job, kwargs={"log": log}, name="comet-upgrade", daemon=True)
    _worker.start()


def start(request, title=None, log=print):
    with _lock:
        st = _load()
        j = st.get("job")
        if j:
            if j["status"] == "running" and _alive(j.get("pid")):
                return f"업그레이드 하나 돌고 있어({j['title']} · {j.get('step', '')}). 「업그레이드 상태」로 볼 수 있어."
            if j["status"] == "ready":
                return f"적용 기다리는 업그레이드가 있어({j['title']}) — 「업그레이드 적용」 또는 「업그레이드 버려」 먼저."
            if j["status"] == "waiting":
                return f"모델 기다리는 업그레이드가 있어({j['title']} · {j.get('retry_at')} 다시) — 「업그레이드 버려」 하고 다시 시켜 줘."
            try:
                _cleanup_wt(j.get("branch"))                 # 실패 · 중간에 꺼진 것의 남은 복사본
            except Exception as e:
                return f"남은 복사본을 못 치워서 시작 안 함: {e}"
            _history(st, j.get("title", "?"), "실패 정리" if j["status"] == "failed" else "중간에 꺼짐 정리")
        title = (title or request)[:30]
        st["job"] = {"title": title, "request": request, "status": "running", "attempts": 0, "notes": []}
        _save(st)
    _launch(log)
    return (f"업그레이드 시작: {title}\n클로드가 떼어 둔 복사본에서 고치고 아스트라가 검토해(막히면 서로 바꾸거나 자기 검토). "
            "몇 분~20분 걸려 — 끝나면 배너 · 텔레그램으로 알려줄게. 진짜 코드엔 「업그레이드 적용」 해야 들어가.")


def tick(log=print):
    """자율 루프가 주기적으로 부른다 — 기다리는 업그레이드 다시 · 중간에 꺼진 것 이어서."""
    st = _load()
    j = st.get("job")
    if not j:
        return
    if j["status"] == "waiting" and j.get("retry_at", "") <= _now():
        log(f"[upgrade] 다시 시도: {j['title']}")
        _launch(log)
    elif j["status"] == "running" and not _alive(j.get("pid")):
        n = int(j.get("attempts", 0)) + 1
        if n >= RETRY_MAX:                                 # 고치다 계속 꺼지면 그만 — 같은 데서 죽는 것일 수 있다
            _finish("failed", f"고치는 중에 {n}번 꺼져서 그만둠", log, keep_wt=True)
            return
        log(f"[upgrade] 중간에 꺼진 업그레이드 이어서: {j['title']}")
        _set_job(attempts=n)
        _launch(log)


# ── 적용 · 되돌리기 · 보기 ───────────────────────────
def _dirty_overlap(files):
    dirty = []
    for l in _git("status", "--porcelain")[1].splitlines():
        p = l[3:].strip().strip('"')
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        if p in files:
            dirty.append(p)
    return dirty


def _daylog(line):
    """그날 작업 기록(logs/날짜-log.md, git 밖)에 한 줄."""
    try:
        p = os.path.join(_DIR, "logs", dt.datetime.now().strftime("%Y-%m-%d") + "-log.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"\n- {dt.datetime.now().strftime('%H:%M')} {line}\n")
    except Exception:
        pass


def apply():
    st = _load()
    j = st.get("job")
    if not j or j["status"] != "ready":
        return "적용할 업그레이드가 없어. " + status()
    files = [p for p in _git("diff", "--name-only", j["commit"] + "~1", j["commit"])[1].splitlines() if p.strip()]
    dirty = _dirty_overlap(files)
    if dirty:
        return ("반영 못 함 — 이 파일에 커밋 안 된 다른 수정이 있어: " + ", ".join(dirty)
                + "\n그 작업을 먼저 커밋하거나 정리하고 다시 「업그레이드 적용」.")
    code, out = _git("cherry-pick", j["commit"])
    if code:
        _git("cherry-pick", "--abort")
        return "그새 같은 곳이 바뀌어서 충돌 — 진짜 코드는 그대로 뒀어. 「업그레이드 다시」 하면 지금 코드 기준으로 다시 고쳐."
    sha = _git("rev-parse", "HEAD")[1].strip()
    with _lock:
        st = _load()
        st["last_applied"] = {"commit": sha, "title": j["title"], "at": _now()}
        _history(st, j["title"], "적용", sha)
        st["job"] = None
        _save(st)
    try:
        _cleanup_wt(j.get("branch"))
    except Exception as e:
        print(f"[upgrade] 복사본 정리 실패: {e}")
    _daylog(f"자가 업그레이드 적용 — {j['title']} (`{sha[:7]}`, 고친 쪽 {j.get('editor')}, 검토 {(j.get('review') or {}).get('by')})")
    return (f"반영했어: {j['title']} (커밋 {sha[:7]})\n"
            "★데몬 재시작해야 실제로 바뀌어 — 데몬 창 닫고 run_daemon.bat (콘솔 코멧도 다시 켜야 함).\n"
            "문제 있으면 「업그레이드 되돌리기」.")


def revert():
    st = _load()
    la = st.get("last_applied")
    if not la:
        return "되돌릴 업그레이드가 없어."
    if _git("merge-base", "--is-ancestor", la["commit"], "HEAD")[0] != 0:
        return f"그 커밋({la['commit'][:7]})이 지금 코드에 없어 — 손으로 봐야 해."
    files = [p for p in _git("diff", "--name-only", la["commit"] + "~1", la["commit"])[1].splitlines() if p.strip()]
    dirty = _dirty_overlap(files)
    if dirty:
        return "되돌리기 못 함 — 이 파일에 커밋 안 된 다른 수정이 있어: " + ", ".join(dirty)
    code, out = _git("revert", "--no-commit", la["commit"])
    if code:
        _git("revert", "--abort")
        return f"되돌리다 충돌 — 그대로 뒀어. 손으로 봐야 해(커밋 {la['commit'][:7]})."
    code, out = _git("commit", "-q", "-m", f"코멧 업그레이드 되돌림 — {la['title']}")
    if code:
        _git("revert", "--abort")
        return "되돌림 커밋 실패: " + out.strip()[-200:]
    with _lock:
        st = _load()
        st["last_applied"] = None
        _history(st, la["title"], "되돌림")
        _save(st)
    _daylog(f"자가 업그레이드 되돌림 — {la['title']}")
    return f"되돌렸어: {la['title']}. 데몬 재시작해야 반영돼."


def discard():
    st = _load()
    j = st.get("job")
    if not j:
        return "버릴 업그레이드가 없어."
    if j["status"] == "running" and _alive(j.get("pid")):
        return "지금 고치는 중이라 못 버려 — 끝나고 「업그레이드 버려」."
    try:
        _cleanup_wt(j.get("branch"))
    except Exception as e:
        return f"복사본을 못 치움: {e}"
    with _lock:
        st = _load()
        _history(st, j["title"], "버림")
        st["job"] = None
        _save(st)
    return f"버렸어: {j['title']} (진짜 코드는 안 건드렸음)"


def redo(log=print):
    st = _load()
    j = st.get("job")
    if not j:
        return "다시 할 업그레이드가 없어."
    if j["status"] == "running" and _alive(j.get("pid")):
        return "지금 고치는 중이야."
    req, title = j["request"], j["title"]
    msg = discard()
    if not msg.startswith("버렸어"):
        return msg
    return start(req, title, log)


def show():
    j = _load().get("job")
    if not j:
        return status()
    rv = j.get("review") or {}
    parts = [f"업그레이드: {j['title']} — {j['status']} · {j.get('step', '')}",
             f"요청: {j['request'][:300]}"]
    if j.get("editor"):
        parts.append(f"고친 쪽: {j['editor']}")
    if j.get("summary"):
        parts.append(j["summary"][:900])
    if j.get("stat"):
        parts.append("바뀐 파일:\n" + j["stat"])
    if j.get("tests"):
        parts.append("시험: " + j["tests"][:400])
    if rv:
        parts.append(f"검토({rv.get('by') or '못 함'}):\n{rv.get('text', '')[:1200]}")
    if j.get("notes"):
        parts.append("과정: " + " / ".join(j["notes"])[:600])
    if j.get("error"):
        parts.append("문제: " + j["error"][:400])
    return "\n\n".join(parts)


def status():
    st = _load()
    j = st.get("job")
    lines = []
    if j:
        extra = f" · {j.get('retry_at')} 다시" if j["status"] == "waiting" else ""
        lines.append(f"지금: {j['title']} — {j['status']} · {j.get('step', '')}{extra}")
    else:
        lines.append("지금 도는 업그레이드 없음.")
    p = st.get("proposals")
    if p and p.get("items"):
        lines.append(f"제안({p['at']} · {p['by']}):")
        lines += [f"  {i}) {x['title']} — {x['why']}" for i, x in enumerate(p["items"], 1)]
    if st.get("last_applied"):
        la = st["last_applied"]
        lines.append(f"마지막 적용: {la['title']} ({la['at']} · {la['commit'][:7]})")
    lines.append("명령: 업그레이드 제안 · 1~3 · 요청 <내용> · 보기 · 적용 · 버려 · 다시 · 되돌리기")
    return "\n".join(lines)


def _push(title, body):
    try:
        import issues
        issues.popup("COMET · 업그레이드", title[:60], body.splitlines()[0][:80] if body else "", level=2, sec=10)
    except Exception as e:
        print(f"[upgrade] 배너 오류: {e}")
    try:
        import notify
        notify.send(f"🛠 {title}\n{body}", html_mode=False)
    except Exception as e:
        print(f"[upgrade] 텔레그램 오류: {e}")


# ── 명령 ─────────────────────────────────────────────
REFUSE = "업그레이드 시작 · 적용 · 되돌리기는 텔레그램에선 안 해 — PC 앞(콘솔이나 폰 웹)에서. 「업그레이드 상태 · 보기」는 여기서도 돼."


def command(t, remote=False, log=print):
    """「업그레이드 …」 명령이면 답, 아니면 None(대화로 흘림 — 「업그레이드 어떻게 해?」 같은 말)."""
    s = t.strip()
    if not s.startswith("업그레이드"):
        return None
    rest = s[len("업그레이드"):].strip()
    key = rest.replace(" ", "")
    if key in ("", "상태"):
        return status()
    if key in ("보기", "내용", "결과"):
        return show()
    if key in ("버려", "취소", "그만"):
        return discard()
    if key in ("제안", "제안해", "제안해줘", "찾아", "찾아줘"):
        threading.Thread(target=lambda: propose(push=True, log=log, force=True), name="upgrade-propose",
                         daemon=True).start()
        return "고칠 거리 찾는 중 — 1~2분 뒤 배너 · 텔레그램으로 제안 보낼게."
    pick = re.fullmatch(r"([1-9])(번)?", key)
    is_start = bool(pick) or rest.startswith("요청")
    if remote and (is_start or key in ("적용", "되돌리기", "되돌려", "다시")):
        return REFUSE
    if key == "적용":
        return apply()
    if key in ("되돌리기", "되돌려"):
        return revert()
    if key == "다시":
        return redo(log)
    if pick:
        p = (_load().get("proposals") or {}).get("items") or []
        n = int(pick.group(1))
        if n > len(p):
            return f"제안 {n}번이 없어. " + status()
        return start(p[n - 1]["request"], p[n - 1]["title"], log)
    if rest.startswith("요청"):
        req = rest[2:].lstrip(" :：").strip()
        if not req:
            return "업그레이드 요청 <고칠 내용> — 예: 업그레이드 요청 할일 목록에 완료한 것도 회색으로 보여 줘"
        return start(req, None, log)
    return None
