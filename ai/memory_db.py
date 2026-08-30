# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 장기 기억 DB (SQLite) + tool calling 도구
#   kind: fact(사실/정보) | memo(메모) | todo(할일)
#   도구: remember / recall / list_todos / complete_todo
#  삭제(forget)는 v1 미포함 — 실수 방지, 나중에 확인절차와 함께.
# ═══════════════════════════════════════════════════════════════
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "comet_memory.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            kind    TEXT NOT NULL,
            content TEXT NOT NULL,
            status  TEXT DEFAULT 'open',
            created TEXT NOT NULL,
            updated TEXT NOT NULL
        )
    """)
    return c


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ═══════════════════════════════════════════════════════════════
#  도구 구현 — 전부 dict 반환 (모델이 결과를 사람 말로 옮김)
# ═══════════════════════════════════════════════════════════════
def remember(kind: str, content: str) -> dict:
    kind = (kind or "memo").strip().lower()
    if kind not in ("fact", "memo", "todo"):
        kind = "memo"
    if not content or not content.strip():
        return {"ok": False, "msg": "내용이 비었음"}
    now = _now()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO memory(kind, content, status, created, updated) VALUES(?,?,?,?,?)",
            (kind, content.strip(), "open", now, now),
        )
    return {"ok": True, "id": cur.lastrowid, "kind": kind, "content": content.strip(),
            "msg": f"{kind} 저장 완료 (#{cur.lastrowid})"}


def recall(query: str = "", limit: int = 5) -> dict:
    limit = max(1, min(int(limit or 5), 20))
    with _conn() as c:
        rows = []
        if query and query.strip():
            for q in _match_candidates(query):
                rows = c.execute(
                    "SELECT id, kind, content, status, updated FROM memory "
                    "WHERE content LIKE ? ORDER BY updated DESC LIMIT ?",
                    (f"%{q}%", limit),
                ).fetchall()
                if rows:
                    break
        else:
            rows = c.execute(
                "SELECT id, kind, content, status, updated FROM memory "
                "ORDER BY updated DESC LIMIT ?",
                (limit,),
            ).fetchall()
    items = [dict(r) for r in rows]
    return {"ok": True, "count": len(items), "items": items}


def list_todos(status: str = "open") -> dict:
    status = (status or "open").strip().lower()
    with _conn() as c:
        if status == "all":
            rows = c.execute(
                "SELECT id, content, status, updated FROM memory "
                "WHERE kind='todo' ORDER BY updated DESC"
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, content, status, updated FROM memory "
                "WHERE kind='todo' AND status=? ORDER BY updated DESC",
                (status,),
            ).fetchall()
    items = [dict(r) for r in rows]
    return {"ok": True, "count": len(items), "items": items}


def _match_candidates(query: str) -> list:
    """전체 문구 → 실패 시 2글자 이상 단어 단위로 느슨하게 매칭"""
    query = query.strip()
    cands = [query] if query else []
    cands += [w for w in query.split() if len(w) >= 2]
    seen, out = set(), []
    for q in cands:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def complete_todo(query: str) -> dict:
    if not query or not query.strip():
        return {"ok": False, "msg": "어느 할일인지 지정 필요"}
    now = _now()
    with _conn() as c:
        row = None
        for q in _match_candidates(query):
            row = c.execute(
                "SELECT id, content FROM memory "
                "WHERE kind='todo' AND status='open' AND content LIKE ? "
                "ORDER BY updated DESC LIMIT 1",
                (f"%{q}%",),
            ).fetchone()
            if row:
                break
        if not row:
            return {"ok": False, "msg": f"'{query}'에 맞는 미완료 할일 없음"}
        c.execute("UPDATE memory SET status='done', updated=? WHERE id=?", (now, row["id"]))
    return {"ok": True, "id": row["id"], "content": row["content"],
            "msg": f"완료 처리: {row['content']}"}


# ═══════════════════════════════════════════════════════════════
#  ollama tool calling 용 스키마 + 디스패처
# ═══════════════════════════════════════════════════════════════
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "새 정보를 장기 기억 DB에 저장/추가/등록한다. "
                           "'기억해','메모해','적어둬','할일로 추가/등록해','저장해' 등 "
                           "새로 넣는 모든 요청은 이 도구. (완료 처리가 아님)",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["fact", "memo", "todo"],
                             "description": "fact=사실/정보, memo=메모, todo=할일"},
                    "content": {"type": "string", "description": "저장할 내용(한국어)"},
                },
                "required": ["kind", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "장기 기억 DB에서 내용을 검색한다. 사용자가 과거에 저장한 것을 "
                           "물어보거나('뭐였지', '기억나?') 정보를 찾을 때 호출.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색어. 비우면 최근 항목."},
                    "limit": {"type": "integer", "description": "최대 개수(기본 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "할일 목록을 보여준다. 사용자가 할 일/투두를 물으면 호출.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "done", "all"],
                               "description": "open=미완료(기본), done=완료, all=전체"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "이미 등록된 할일을 다 끝냈을 때만 완료 처리한다. "
                           "'~끝냈어','~했어','~완료'. 새로 추가하는 게 아니라 "
                           "기존 항목을 닫는 것임.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "완료할 할일을 식별하는 키워드"},
                },
                "required": ["query"],
            },
        },
    },
]

_DISPATCH = {
    "remember": remember,
    "recall": recall,
    "list_todos": list_todos,
    "complete_todo": complete_todo,
}


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
