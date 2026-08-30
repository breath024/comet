# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 프로젝트 브리핑 (읽기 전용)
#   호윤이 "진행 중인 일"을 골라서 브리핑받기 위한 통로.
#   각 프로젝트의 살아있는 핸드오프 문서를 그 자리에서 읽어
#   "현재 상태 / 다음 할 일"을 모델이 요약하게 한다.
#   ※ 프로필 스냅샷(SYSTEM에 박힌 고정 메모리)이 아니라 실시간 문서다.
# ═══════════════════════════════════════════════════════════════
import os
import time

import localcfg

HOME = os.path.expanduser("~")
DESK = os.path.join(HOME, "Desktop")
ICLOUD = os.path.join(HOME, "iCloudDrive")

BRIEF_CHARS = 3500   # 브리핑에 넘길 문서 앞부분 글자 수(핸드오프는 최신이 맨 위)

# ── 진행 중인 일 레지스트리 ──────────────────────────────────────
#   doc = 상태 문서들(앞에서부터 우선). aliases = 호윤이 부를 법한 말.
PROJECTS = {
    "coinbot": {
        "name": "코인봇",
        "one_line": "코인 선물 자동매매 봇(100x 격리). 거래기록 누적 중.",
        "aliases": ["코인봇", "코인 봇", "코인", "선물봇", "선물 봇", "비트봇", "coinbot"],
        "docs": [os.path.join(DESK, "창업", "코인봇", "HANDOFF.md"),
                 os.path.join(DESK, "창업", "코인봇", "README.md")],
    },
    "stockbot": {
        "name": "주식봇",
        "one_line": "한국 주식 자동매매 + 가상매매(백테스트/모의).",
        "aliases": ["주식봇", "주식 봇", "주식", "stockbot"],
        "docs": [os.path.join(DESK, "창업", "주식봇", "HANDOFF.md"),
                 os.path.join(DESK, "창업", "주식봇", "README.md")],
    },
    "contentforge": {
        "name": "ContentForge",
        "one_line": "주제 한 줄 → AI 카드뉴스 자동생성 (오파독 클론).",
        "aliases": ["contentforge", "content forge", "콘텐츠포지", "카드뉴스", "콘텐트포지", "포지"],
        "docs": [os.path.join(DESK, "ContentForge", "README.md")],
    },
    "onlymoney": {
        "name": "Only Money",
        "one_line": "단일 HTML 투자 대시보드 (iCloud 동기화).",
        "aliases": ["only money", "onlymoney", "온리머니", "대시보드", "투자대시보드", "머니"],
        "docs": [os.path.join(ICLOUD, "Only Money", "HANDOFF.md")],
    },
    "chinese": {
        "name": "중국어 SRS",
        "one_line": "회화 위주 중국어 학습 앱(데이터 팩 분리).",
        "aliases": ["중국어", "중국어srs", "중국어 공부", "회화", "srs", "chinese"],
        "docs": [os.path.join(DESK, "중국어 공부", "CLAUDE.md"),
                 os.path.join(DESK, "중국어 공부", "README.md")],
    },
}

# 개인 경로가 들어가는 항목(업무 프로젝트 등)은 저장소에 올리지 않고 여기서 합친다.
#  local_config.json 의 extra_projects — 파일이 없으면 아무 일도 없다.
for _k, _c in (localcfg.get("extra_projects") or {}).items():
    PROJECTS[_k] = {
        "name": _c.get("name", _k),
        "one_line": _c.get("one_line", ""),
        "aliases": _c.get("aliases", []),
        "docs": [os.path.expanduser(d) for d in _c.get("docs", [])],
    }


def _resolve(q: str) -> str:
    """호윤 표현 → 프로젝트 키. 별칭 부분일치, 못 찾으면 ''."""
    s = (q or "").lower().replace(" ", "").strip()
    if not s:
        return ""
    # 1) 키/이름 직접 일치
    for key, cfg in PROJECTS.items():
        if key in s or cfg["name"].lower().replace(" ", "") in s:
            return key
    # 2) 별칭 부분일치
    for key, cfg in PROJECTS.items():
        for a in cfg["aliases"]:
            if a.replace(" ", "") in s:
                return key
    return ""


def _first_existing_doc(cfg):
    for d in cfg["docs"]:
        if os.path.isfile(d):
            return d
    return None


def _mtime_str(path):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
    except Exception:
        return "?"


# ── 공개 도구 ────────────────────────────────────────────────
def list_projects() -> dict:
    """진행 중인 일 목록 + 한 줄 상태 + 문서 최종 수정일."""
    items = []
    for key, cfg in PROJECTS.items():
        doc = _first_existing_doc(cfg)
        items.append({
            "키": key,
            "이름": cfg["name"],
            "한줄": cfg["one_line"],
            "문서있음": doc is not None,
            "갱신일": _mtime_str(doc) if doc else "없음",
        })
    return {"ok": True, "items": items}


def brief(project: str = "") -> dict:
    """특정 프로젝트의 살아있는 상태 문서를 읽어 브리핑 소스로 반환."""
    key = _resolve(project)
    if not key:
        names = " / ".join(c["name"] for c in PROJECTS.values())
        return {"ok": False,
                "msg": f"어느 걸 브리핑할까요. 진행 중: {names}. (예: '코인봇 브리핑')"}
    cfg = PROJECTS[key]
    doc = _first_existing_doc(cfg)
    if not doc:
        return {"ok": False,
                "msg": f"{cfg['name']} 상태 문서를 못 찾음 ({cfg['docs'][0]})"}
    try:
        with open(doc, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(BRIEF_CHARS + 1)
    except Exception as e:
        return {"ok": False, "msg": f"{cfg['name']} 문서 읽기 실패: {e}"}
    return {
        "ok": True,
        "프로젝트": cfg["name"],
        "한줄": cfg["one_line"],
        "갱신일": _mtime_str(doc),
        "문서": os.path.basename(doc),
        "잘림": len(text) > BRIEF_CHARS,
        "내용": text[:BRIEF_CHARS],
    }


ACTIONS = {"brief", "list_projects"}
_DISPATCH = {"brief": brief, "list_projects": list_projects}


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
