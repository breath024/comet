# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET-Coder · 코딩 도구함 (읽기 + 쓰기 + 셸)
#
#  files.py(읽기 전용)와 별개. 여기는 에이전트가 실제로 "고치고 돌리는" 손이다.
#  안전 원칙(호윤 규칙 — 멋대로 고쳐 날린 적 있음):
#   · 읽기/목록/검색 = 자유.
#   · 쓰기/수정/삭제/셸 = 기본적으로 confirm() 으로 사람 승인 후 실행.
#   · auto_approve=True 일 때만 확인 없이 진행(스스로 켜지 않음, 사람이 토글).
#   · 모든 쓰기는 .comet_bak/ 에 직전 버전을 백업 → 되돌릴 수 있음.
# ═══════════════════════════════════════════════════════════════
import os
import re
import shutil
import difflib
import subprocess
import fnmatch

HOME = os.path.expanduser("~")
MAX_READ = 20000          # 한 번에 읽는 글자 상한(코드라 files.py보다 크게)
WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "AppData", ".comet_bak"}

# 되돌리기 어려운 위험 셸 명령 — auto 모드에선 차단, 신중모드에선 강한 확인.
DANGEROUS = [
    (r"\brm\s+(-\w*[rf]\w*|.*-[rf])", "rm 삭제"),
    (r"\b(del|erase)\b.*(/s|/q|\*)", "del 대량삭제"),
    (r"\b(rmdir|rd)\b.*/s", "디렉터리 삭제"),
    (r"remove-item.*(-recurse|-force|-r\b|-fo)", "Remove-Item 삭제"),
    (r"\bformat\b\s+[a-z]:", "디스크 포맷"),
    (r"\bgit\s+reset\s+--hard", "git reset --hard(변경 날림)"),
    (r"\bgit\s+clean\s+-\w*f", "git clean(미추적 삭제)"),
    (r"\bgit\s+checkout\s+--\s", "git checkout --(변경 폐기)"),
    (r"\b(shutdown|reboot|taskkill\s+/f|diskpart|mkfs|fdisk)\b", "시스템/프로세스"),
    (r">\s*/dev/sd|of=/dev/", "디스크 직접 쓰기"),
    (r":\(\)\s*\{.*\|.*&\s*\}", "포크 폭탄"),
]


def _danger_reason(cmd):
    low = cmd.lower()
    for pat, label in DANGEROUS:
        if re.search(pat, low):
            return label
    return None


def _confirm_always(_desc):     # 기본 확인 함수(콘솔). 루프가 교체해서 주입.
    try:
        ans = input(f"  ⚠ {_desc}\n  진행할까? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes", "ㅇ", "응")


class ToolBox:
    """에이전트가 쓰는 도구함. 작업 루트와 확인 콜백을 들고 있다."""

    def __init__(self, root=None, confirm=None, auto_approve=False):
        self.root = os.path.abspath(root or os.getcwd())
        self.confirm = confirm or _confirm_always
        self.auto_approve = auto_approve

    # ── 경로 해석: 절대면 그대로, 상대면 root 기준 ──────────────
    def _path(self, path):
        p = (path or "").strip().strip('"').strip("'")
        if os.path.isabs(p):
            return os.path.normpath(p)
        return os.path.normpath(os.path.join(self.root, p))

    def _backup(self, real):
        if not os.path.isfile(real):
            return None
        bak_dir = os.path.join(os.path.dirname(real), ".comet_bak")
        os.makedirs(bak_dir, exist_ok=True)
        dest = os.path.join(bak_dir, os.path.basename(real) + ".bak")
        try:
            shutil.copy2(real, dest)
            return dest
        except Exception:
            return None

    def _ask(self, desc):
        if self.auto_approve:
            return True
        return self.confirm(desc)

    def _outside_root(self, real):
        try:
            return os.path.commonpath([os.path.abspath(real), self.root]) != self.root
        except ValueError:
            return True          # 다른 드라이브 등 — 밖으로 간주

    def _diff(self, old, new, name):
        d = difflib.unified_diff(old.splitlines(), new.splitlines(),
                                 fromfile=name + " (전)", tofile=name + " (후)",
                                 lineterm="")
        return "\n".join(d)[:2500]

    # 쓰기 승인: 작업폴더 밖이면 auto 라도 차단, 신중모드는 경고와 함께 확인
    def _approve_write(self, real, desc):
        if self._outside_root(real):
            if self.auto_approve:
                return False, (f"작업폴더 밖 경로는 자동승인 불가: {real}\n"
                               "신중모드(run_coder.bat)에서 직접 확인하며 실행해.")
            if not self.confirm("⚠ 작업폴더 밖! " + desc):
                return False, "사용자가 거부함"
            return True, None
        if not self._ask(desc):
            return False, "사용자가 거부함"
        return True, None

    # ── 읽기 계열 (확인 불필요) ─────────────────────────────────
    def read_file(self, path):
        real = self._path(path)
        if not os.path.isfile(real):
            return {"ok": False, "msg": f"파일 없음: {real}"}
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(MAX_READ + 1)
        except Exception as e:
            return {"ok": False, "msg": f"읽기 실패: {e}"}
        return {"ok": True, "path": real, "truncated": len(text) > MAX_READ,
                "content": text[:MAX_READ]}

    def list_dir(self, path=""):
        real = self._path(path) if path else self.root
        if not os.path.isdir(real):
            return {"ok": False, "msg": f"폴더 없음: {real}"}
        items = []
        for name in sorted(os.listdir(real)):
            full = os.path.join(real, name)
            items.append(name + ("/" if os.path.isdir(full) else ""))
        return {"ok": True, "path": real, "items": items[:300]}

    def search_files(self, query, limit=30):
        q = (query or "").strip().lower()
        if not q:
            return {"ok": False, "msg": "검색어 필요"}
        hits = []
        for dp, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP and not d.startswith(".")]
            for f in files:
                if q in f.lower() or fnmatch.fnmatch(f.lower(), q):
                    hits.append(os.path.relpath(os.path.join(dp, f), self.root))
            if len(hits) >= limit:
                break
        return {"ok": True, "count": len(hits), "items": hits[:limit]}

    def grep(self, pattern, glob="*", limit=60):
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return {"ok": False, "msg": f"정규식 오류: {e}"}
        hits = []
        for dp, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP and not d.startswith(".")]
            for f in files:
                if not fnmatch.fnmatch(f, glob):
                    continue
                fp = os.path.join(dp, f)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                        for n, line in enumerate(fh, 1):
                            if rx.search(line):
                                rel = os.path.relpath(fp, self.root)
                                hits.append(f"{rel}:{n}: {line.rstrip()[:200]}")
                                if len(hits) >= limit:
                                    return {"ok": True, "count": len(hits), "items": hits}
                except Exception:
                    continue
        return {"ok": True, "count": len(hits), "items": hits}

    # ── 쓰기 계열 (확인 필요) ───────────────────────────────────
    def write_file(self, path, content):
        real = self._path(path)
        exists = os.path.isfile(real)
        verb = "덮어쓰기" if exists else "새 파일 생성"
        diff = ""
        if exists:
            try:
                old = open(real, "r", encoding="utf-8", errors="replace").read()
                diff = self._diff(old, content, os.path.basename(real))
            except Exception:
                pass
        desc = f"{verb}: {real} ({len(content)}자)" + (f"\n{diff}" if diff else "")
        okay, why = self._approve_write(real, desc)
        if not okay:
            return {"ok": False, "msg": why, "skipped": True}
        bak = self._backup(real)
        try:
            os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
            with open(real, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            return {"ok": False, "msg": f"쓰기 실패: {e}"}
        return {"ok": True, "msg": f"{verb} 완료: {real}", "backup": bak,
                "diff": diff or "(새 파일)"}

    def edit_file(self, path, old, new):
        real = self._path(path)
        if not os.path.isfile(real):
            return {"ok": False, "msg": f"파일 없음: {real}"}
        try:
            with open(real, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            return {"ok": False, "msg": f"읽기 실패: {e}"}
        cnt = text.count(old)
        if cnt == 0:
            return {"ok": False, "msg": "찾는 문자열이 없음(공백·들여쓰기까지 정확히 일치해야 함)"}
        if cnt > 1:
            return {"ok": False, "msg": f"문자열이 {cnt}곳에 있음 — 더 길고 고유하게 지정해."}
        updated = text.replace(old, new)
        diff = self._diff(text, updated, os.path.basename(real))
        okay, why = self._approve_write(real, f"수정: {real} (1곳 치환)" + (f"\n{diff}" if diff else ""))
        if not okay:
            return {"ok": False, "msg": why, "skipped": True}
        bak = self._backup(real)
        try:
            with open(real, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
        except Exception as e:
            return {"ok": False, "msg": f"쓰기 실패: {e}"}
        return {"ok": True, "msg": f"수정 완료: {real}", "backup": bak, "diff": diff}

    def run_shell(self, command, cwd=None, timeout=120):
        wd = self._path(cwd) if cwd else self.root
        danger = _danger_reason(command)
        if danger:
            if self.auto_approve:
                return {"ok": False, "msg": f"위험 명령 차단(자동승인 모드, {danger}): {command}\n"
                                            "되돌리기 어려운 명령이라 자동으로 안 돌려. "
                                            "신중모드(run_coder.bat)에서 직접 확인하며 실행해."}
            if not self.confirm(f"⚠⚠ 위험 명령({danger}) — 정말 실행?\n{command}  (cwd={wd})"):
                return {"ok": False, "msg": "사용자가 거부함", "skipped": True}
        elif not self._ask(f"셸 실행: {command}  (cwd={wd})"):
            return {"ok": False, "msg": "사용자가 거부함", "skipped": True}
        # 윈도우 한글 깨짐 방지: 자식 프로세스가 stdout 을 UTF-8 로 뱉게 강제
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run(command, shell=True, cwd=wd, capture_output=True,
                               text=True, timeout=timeout, encoding="utf-8",
                               errors="replace", env=env)
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": f"시간초과({timeout}s)"}
        except Exception as e:
            return {"ok": False, "msg": f"실행 실패: {e}"}
        out = (p.stdout or "")[-6000:]
        err = (p.stderr or "")[-3000:]
        return {"ok": p.returncode == 0, "code": p.returncode,
                "stdout": out, "stderr": err}


# ── 도구 스키마(에이전트 프롬프트에 넣는 설명) ────────────────────
TOOL_SPEC = """사용 가능한 도구:
- read_file(path)               파일 내용 읽기
- list_dir(path)                폴더 목록
- search_files(query)           파일명으로 찾기 (와일드카드 *)
- grep(pattern, glob)           파일 내용에서 정규식 검색 (glob 예: "*.py")
- write_file(path, content)     파일 새로 쓰기/덮어쓰기 (자동 백업)
- edit_file(path, old, new)     파일에서 old 문자열 1곳을 new 로 치환 (공백까지 정확히)
- run_shell(command, cwd)       셸 명령 실행 (stdout/stderr/exit code 반환)
- done(summary)                 작업 끝. summary 에 한 일 요약."""

DISPATCH = {"read_file", "list_dir", "search_files", "grep",
            "write_file", "edit_file", "run_shell"}


def call(box: "ToolBox", name: str, args: dict) -> dict:
    args = args or {}
    fn = getattr(box, name, None)
    if name not in DISPATCH or fn is None:
        return {"ok": False, "msg": f"알 수 없는 도구: {name}"}
    try:
        return fn(**args)
    except TypeError as e:
        return {"ok": False, "msg": f"인자 오류: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"실행 오류: {e}"}
