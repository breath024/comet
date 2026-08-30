# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 파일/폴더 도구 (읽기 전용 v1)
#   list_dir / read_file / search_files
#  쓰기·이동·삭제는 미포함 — LLM 실수 방지, 나중에 확인절차와 함께.
#  한국어 경로("바탕화면", "창업")를 별칭+검색으로 실제 경로로 해석.
# ═══════════════════════════════════════════════════════════════
import os

HOME     = os.path.expanduser("~")
BASE_DIR = os.path.join(HOME, "Desktop")     # 기본 탐색 기준 (호윤 프로젝트가 여기 있음)
MAX_READ = 4000                               # 파일 읽기 글자 상한
WALK_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "AppData"}

ALIASES = {
    "바탕화면": os.path.join(HOME, "Desktop"),
    "데스크탑": os.path.join(HOME, "Desktop"),
    "desktop":  os.path.join(HOME, "Desktop"),
    "문서":     os.path.join(HOME, "Documents"),
    "documents": os.path.join(HOME, "Documents"),
    "다운로드": os.path.join(HOME, "Downloads"),
    "downloads": os.path.join(HOME, "Downloads"),
    "홈":       HOME,
    "home":     HOME,
}


# ── 경로 해석: 별칭 → 절대 → BASE 상대 → 이름 검색 ──────────────
def _search_name(name, limit=1):
    name_low = name.lower()
    matches = []
    for dirpath, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in WALK_SKIP and not d.startswith(".")]
        if BASE_DIR != dirpath and dirpath[len(BASE_DIR):].count(os.sep) > 4:
            dirs[:] = []                     # 깊이 제한
            continue
        for d in dirs:
            if name_low in d.lower():
                matches.append(os.path.join(dirpath, d))
        for f in files:
            if name_low in f.lower():
                matches.append(os.path.join(dirpath, f))
        if len(matches) >= limit:
            break
    return matches


def _resolve(path):
    if not path or not path.strip():
        return BASE_DIR
    p = path.strip().strip('"').strip("'")
    low = p.lower()

    if low in ALIASES:
        return ALIASES[low]
    # "바탕화면/창업" 같은 별칭+하위
    for a, base in ALIASES.items():
        if low.startswith(a):
            rest = p[len(a):].lstrip("/\\ ")
            cand = os.path.join(base, rest) if rest else base
            if os.path.exists(cand):
                return cand
    if os.path.isabs(p) and os.path.exists(p):
        return p
    cand = os.path.join(BASE_DIR, p)
    if os.path.exists(cand):
        return cand
    found = _search_name(p, limit=1)
    return found[0] if found else None


# ── 도구 구현 ────────────────────────────────────────────────
def list_dir(path: str = "") -> dict:
    real = _resolve(path)
    if not real or not os.path.isdir(real):
        return {"ok": False, "msg": f"'{path}' 폴더를 못 찾음"}
    items = []
    for name in sorted(os.listdir(real)):
        full = os.path.join(real, name)
        if os.path.isdir(full):
            items.append({"name": name, "type": "폴더"})
        else:
            items.append({"name": name, "type": "파일",
                          "kb": round(os.path.getsize(full) / 1024, 1)})
    return {"ok": True, "path": real, "count": len(items), "items": items[:100]}


def read_file(path: str) -> dict:
    real = _resolve(path)
    if not real or not os.path.isfile(real):
        return {"ok": False, "msg": f"'{path}' 파일을 못 찾음"}
    try:
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(MAX_READ + 1)
    except Exception as e:
        return {"ok": False, "msg": f"읽기 실패: {e}"}
    return {"ok": True, "path": real, "truncated": len(text) > MAX_READ,
            "content": text[:MAX_READ]}


def search_files(query: str, limit: int = 15) -> dict:
    if not query or not query.strip():
        return {"ok": False, "msg": "검색어 필요"}
    limit = max(1, min(int(limit or 15), 50))
    matches = _search_name(query.strip(), limit=limit)
    rel = [m[len(BASE_DIR):].lstrip(os.sep) for m in matches]
    return {"ok": True, "query": query, "base": BASE_DIR,
            "count": len(rel), "items": rel}


ACTIONS = {"list_dir", "read_file", "search_files"}
_DISPATCH = {"list_dir": list_dir, "read_file": read_file, "search_files": search_files}


def dispatch(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"ok": False, "msg": f"알 수 없는 도구: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"ok": False, "msg": f"인자 오류: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"실행 오류: {e}"}
