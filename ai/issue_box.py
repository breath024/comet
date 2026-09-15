# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 이슈 접수함 — 쓰다가 이상한 걸 한 줄로 던져두면 쌓아두고, 고쳐지면 닫는다
#
#   이슈 웹검색 결과가 이상함      → 접수 (#번호)
#   이슈 / 이슈 목록               → 열린 것
#   이슈 전체                      → 닫힌 것까지
#   이슈 닫기 3 [해결 내용]         → 닫기
#   이슈 다시 3                    → 다시 열기
#
#  저장: comet_memory.db 의 memory 테이블(kind='issue') — 기억 DB 를 따로 안 만든다.
#   · recall(기억 검색)에도 같이 걸린다 — "그 웹검색 문제 어떻게 됐지?" 에 답할 수 있게.
#   · tag = 어느 기능 문제인지(웹검색/음성/알림…) 자동 추정. 목록을 기능별로 묶어 보여준다.
#  바뀔 때마다 ISSUES.md 로 내보낸다 — 클로드가 COMET 손대기 전에 HANDOFF 와 같이 읽는 용도.
# ═══════════════════════════════════════════════════════════════
import os
import re
from datetime import datetime

import memory_db

_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(_DIR, "ISSUES.md")

# 기능 추정 — 앞에서부터 먼저 걸리는 것
AREAS = [
    ("웹검색", r"웹\s*검색|검색|덕덕고|구글|서치|searx"),
    ("알림",   r"알림|배너|토스트|이슈 알림|푸시|텔레그램"),
    ("음성",   r"음성|목소리|tts|말하기|듣기|마이크|whisper|웨이크|호출어"),
    ("시세",   r"시세|주가|환율|코인|코스피|나스닥|종목|분석"),
    ("브리핑", r"브리핑|현황|진행중|프로젝트"),
    ("기억",   r"기억|메모|할일|까먹|잊"),
    ("화면",   r"화면|스크린|이미지|사진|vision"),
    ("폰·데몬", r"폰|데몬|웹ui|원격|tailscale|접속"),
    ("대답",   r"대답|답변|말귀|멍청|빈\s*답|느려|느림|영어로"),
]


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    c = memory_db._conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(memory)")}
    if "tag" not in cols:                       # 기존 DB 에 컬럼만 덧붙인다(데이터 보존)
        c.execute("ALTER TABLE memory ADD COLUMN tag TEXT")
    return c


def guess_area(text):
    low = (text or "").lower()
    for name, pat in AREAS:
        if re.search(pat, low):
            return name
    return "기타"


def add(content, tag=None):
    content = (content or "").strip()
    if not content:
        return {"ok": False, "msg": "무슨 이슈인지 같이 적어줘. 예: 이슈 웹검색 결과가 이상함"}
    tag = tag or guess_area(content)
    with _conn() as c:
        dup = c.execute("SELECT id FROM memory WHERE kind='issue' AND status='open' AND content=?",
                        (content,)).fetchone()
        if dup:
            return {"ok": True, "id": dup["id"], "msg": f"이미 열려 있는 이슈야 (#{dup['id']})."}
        now = _now()
        cur = c.execute("INSERT INTO memory(kind, content, status, created, updated, tag) "
                        "VALUES('issue', ?, 'open', ?, ?, ?)", (content, now, now, tag))
        iid = cur.lastrowid
    export()
    return {"ok": True, "id": iid, "msg": f"이슈 #{iid} 접수 [{tag}] — {content}"}


def _rows(status="open"):
    q = "SELECT id, content, status, created, updated, tag FROM memory WHERE kind='issue'"
    args = ()
    if status != "all":
        q += " AND status=?"
        args = (status,)
    with _conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id DESC", args)]


def listing(status="open"):
    rows = _rows(status)
    if not rows:
        return "열린 이슈 없음." if status == "open" else "이슈 기록 없음."
    groups = {}
    for r in rows:
        groups.setdefault(r.get("tag") or "기타", []).append(r)
    head = f"열린 이슈 {len(rows)}건" if status == "open" else f"이슈 전체 {len(rows)}건"
    lines = [head]
    for tag, rs in groups.items():
        lines.append(f"[{tag}]")
        for r in rs:
            mark = "" if r["status"] == "open" else " (닫힘)"
            lines.append(f"  #{r['id']} {r['content']}{mark}  · {r['created'][5:10]}")
    return "\n".join(lines)


def close(iid, note=""):
    with _conn() as c:
        row = c.execute("SELECT content, status FROM memory WHERE kind='issue' AND id=?",
                        (iid,)).fetchone()
        if not row:
            return {"ok": False, "msg": f"#{iid} 이슈 없음."}
        if row["status"] != "open":
            return {"ok": False, "msg": f"#{iid} 는 이미 닫혀 있어."}
        content = row["content"] + (f"  → 해결: {note.strip()}" if note.strip() else "")
        c.execute("UPDATE memory SET status='done', content=?, updated=? WHERE id=?",
                  (content, _now(), iid))
    export()
    return {"ok": True, "msg": f"#{iid} 닫음 — {content}"}


def reopen(iid):
    with _conn() as c:
        row = c.execute("SELECT content FROM memory WHERE kind='issue' AND id=? AND status!='open'",
                        (iid,)).fetchone()
        n = 0
        if row:
            # "→ 해결: …" 을 그대로 두면 열린 목록에서 고쳐진 것처럼 읽힌다 → 재발 표시로 바꾼다
            content = row["content"].replace("→ 해결:", "→ 재발 (전에 한 조치:", 1)
            if content != row["content"]:
                content += ")"
            n = c.execute("UPDATE memory SET status='open', content=?, updated=? WHERE id=?",
                          (content, _now(), iid)).rowcount
    export()
    return {"ok": bool(n), "msg": f"#{iid} 다시 열었어." if n else f"#{iid} 는 없거나 이미 열려 있어."}


def export():
    try:
        rows = _rows("all")
        opened = [r for r in rows if r["status"] == "open"]
        closed = [r for r in rows if r["status"] != "open"][:30]
        out = ["# COMET 이슈 접수함",
               "",
               "> 자동 생성 — `issue_box.py` 가 이슈가 바뀔 때마다 덮어쓴다. 직접 고치지 말 것.",
               "> 호윤이 COMET 에 `이슈 …` 로 던져둔 것. **COMET 손대기 전에 HANDOFF 와 같이 읽기.**",
               "> 닫기: COMET 에 `이슈 닫기 번호 해결내용`",
               "",
               f"## 열린 것 ({len(opened)})", ""]
        out += [f"- **#{r['id']}** [{r.get('tag') or '기타'}] {r['content']}  _(접수 {r['created'][:10]})_"
                for r in opened] or ["- 없음"]
        out += ["", f"## 최근 닫힌 것 ({len(closed)})", ""]
        out += [f"- ~~#{r['id']}~~ [{r.get('tag') or '기타'}] {r['content']}  _(닫음 {r['updated'][:10]})_"
                for r in closed] or ["- 없음"]
        with open(EXPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        pass


def command(text):
    """comet.respond 가 '이슈…' 로 시작하면 넘긴다. 처리 안 할 문장이면 None."""
    t = (text or "").strip()
    body = re.sub(r"^이슈\s*", "", t, count=1)
    flat = body.replace(" ", "")
    if flat in ("", "목록", "리스트", "보여줘", "뭐있어", "뭐있어?"):
        return listing("open")
    if flat in ("전체", "전부", "다", "닫힌것"):
        return listing("all")
    m = re.match(r"^(?:닫기|닫아|해결|close)\s*#?(\d+)\s*(.*)$", body) or \
        re.match(r"^#?(\d+)\s*(?:닫기|닫아|해결|close)\s*(.*)$", body)
    if m:
        return close(int(m.group(1)), m.group(2))["msg"]
    m = re.match(r"^(?:다시|열기|reopen)\s*#?(\d+)$", flat) or re.match(r"^#?(\d+)(?:다시|다시열기)$", flat)
    if m:
        return reopen(int(m.group(1)))["msg"]
    # 목록을 묻는 짧은 말("이슈 요즘 뭐 있어?", "이슈 몇 개야")은 목록으로.
    # 길이 제한: "이슈 검색 결과 목록이 이상함" 같은 접수를 목록 요청으로 오인하지 않게.
    if len(flat) <= 10 and re.search(r"뭐있|몇개|남은|목록|보여", flat):
        return listing("open")
    # "이슈가 뭐야?" 같은 일반 질문은 접수하지 않는다 — '이슈' 뒤에 조사가 붙거나 물음표면 대화로 흘림
    if re.match(r"^이슈(가|는|를|란|라는|들|야|요)", t) or t.endswith("?"):
        return None
    return add(body)["msg"]
