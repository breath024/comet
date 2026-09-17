# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 할일·일정 창구 — 호윤 일감을 프로젝트별로 던져두고, 마감·시각으로 되찾는다
#
#   할일 RawCut 납품 영상 내일까지        → 접수 (#번호, 프로젝트·마감 자동)
#   할일 / 할일 목록                      → 열린 것 (프로젝트별, 마감 임박순)
#   할일 오늘 / 할일 이번주                → 그 기간 것만 시간순으로
#   일정                                  → 시각이 붙은 것만 (약속·미팅)
#   할일 RawCut                           → 그 프로젝트 것만
#   할일 완료 3 / 할일 완료 납품           → 닫기 (번호 또는 키워드)
#   할일 다시 3                           → 다시 열기
#   할일 전체                             → 닫힌 것까지
#
#  저장: comet_memory.db 의 memory 테이블(kind='todo') — issue_box 와 같은 테이블.
#   · 기존 memory_db 의 도구(remember/list_todos/complete_todo/recall)와 **같은 칸**을 쓴다.
#     즉 모델이 대화 중에 저장한 할일도 여기서 보이고, 여기서 넣은 것도 recall 에 걸린다.
#     입구만 둘(대화=모델이 고름 / 명령=여기, 모델 0). 저장소를 쪼개면 "어디 적었더라"가 된다.
#   · tag = 어느 프로젝트 일인지 자동 추정(issue_box 의 tag 는 COMET 기능 분류, 여긴 프로젝트).
#   · due = 'YYYY-MM-DD'(마감) 또는 'YYYY-MM-DDTHH:MM'(시각 있는 일정). 없으면 NULL.
#  ※ 삭제는 없다 — memory_db 가 처음부터 forget 을 뺀 이유와 같다(실수 방지). 닫기로 충분.
# ═══════════════════════════════════════════════════════════════
import os
import re
from datetime import datetime, date, timedelta

import memory_db

_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.join(_DIR, "TODO.md")

# ── 프로젝트 추정 — 앞에서부터 먼저 걸리는 것 ──────────────────────────────
#   호윤이 실제로 돌리는 것들. 새 프로젝트가 생기면 여기 한 줄.
PROJECTS = [
    ("RawCut",      r"rawcut|로컷|롤컷|가편집|편집본|스트리머|납품|편집앱|제품소개서"),
    ("코인봇",       r"코인봇|선물봇|매매법|백테스트|모의투자"),
    ("주식봇",       r"주식봇|자동매매"),
    ("IN-POS",      r"in-?pos|인포스|포스기|이상감지"),
    ("Only Money",  r"온리머니|only\s*money|대시보드"),
    ("ContentForge", r"contentforge|컨텐트포지|콘텐츠포지|카드뉴스|숏폼자동"),
    ("MR.AiCE",     r"mr\.?aice|미스터에이스|상표|특허|출원"),
    ("Totem",       r"토템|totem|굿즈|쉽샵|ship\s*shop"),
    ("COMET",       r"코멧|comet|자비스"),
    ("VOID",        r"void|abyss|보이드|마스크"),
    ("HELM",        r"helm|헬름|클로드독|claudedock"),
    ("크몽",         r"크몽|kmong|외주"),
    ("학교",         r"학교|시험|과제|텀프|수업|학점|인하공전|tcp/?ip|중간고사|기말"),
    ("중국어",       r"중국어|hsk|회화"),
    ("알바·노무",    r"알바|편의점|야간|노무|주휴|근로계약"),
    ("창업지원",     r"지원사업|사업계획서|경진|캠프|멘토링|ir|피칭|대회"),
]

# ── 날짜·시각 표현 ────────────────────────────────────────────────────────
_WEEKDAY = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_REL = {"오늘": 0, "낼": 1, "내일": 1, "모레": 2, "글피": 3}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _conn():
    c = memory_db._conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(memory)")}
    if "tag" not in cols:                       # issue_box 가 먼저 안 돌았을 수도 있다
        c.execute("ALTER TABLE memory ADD COLUMN tag TEXT")
    if "due" not in cols:                       # 기존 데이터 보존하고 칸만 덧붙인다
        c.execute("ALTER TABLE memory ADD COLUMN due TEXT")
    return c


def guess_project(text):
    low = (text or "").lower()
    for name, pat in PROJECTS:
        if re.search(pat, low):
            return name
    return "기타"


# ═══════════════════════════════════════════════════════════════
#  한국어 한 줄에서 마감·시각 뽑기 (모델 안 씀 — 파이썬 규칙)
# ═══════════════════════════════════════════════════════════════
def parse_when(text, today=None):
    """'납품 영상 내일 3시까지' → ('2026-09-18T15:00', '납품 영상')

    반환 (due, rest). due 없으면 (None, 원문).
    날짜 표현은 본문에서 빼준다 — 목록에서 '내일 내일 내일'이 겹쳐 보이지 않게.
    """
    s = text or ""
    today = today or date.today()
    d = None
    t = None

    def cut(m):
        nonlocal s
        s = (s[:m.start()] + " " + s[m.end():])

    # ── 날짜 ──
    m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            cut(m)
        except ValueError:
            d = None
    if d is None:
        m = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", s)
        if m:
            d = _mk_md(today, int(m.group(1)), int(m.group(2)))
            if d:
                cut(m)
    if d is None:
        # 9/20, 9.20 — 슬래시·점이 있어야 날짜로 본다("9월 매출 정리"는 날짜 아님)
        m = re.search(r"\b(\d{1,2})[/.](\d{1,2})\b", s)
        if m:
            d = _mk_md(today, int(m.group(1)), int(m.group(2)))
            if d:
                cut(m)
    if d is None:
        # "20일까지" — 달 없이 일만. 이미 지난 날이면 다음 달로 본다.
        m = re.search(r"\b(\d{1,2})\s*일(?!\s*간|째|치)", s)
        if m:
            day = int(m.group(1))
            d = _mk_md(today, today.month, day, roll_month=True)
            if d:
                cut(m)
    if d is None:
        m = re.search(r"(오늘|내일|낼|모레|글피)", s)
        if m:
            d = today + timedelta(days=_REL[m.group(1)])
            cut(m)
    if d is None:
        m = re.search(r"(이번\s*주|담주|다음\s*주)?\s*([월화수목금토일])\s*요일", s)
        if m:
            target = _WEEKDAY[m.group(2)]
            ahead = (target - today.weekday()) % 7
            if ahead == 0:
                ahead = 7                      # "월요일"을 오늘(월)에 말하면 다음 월요일
            if m.group(1) and "다음" in m.group(1) or (m.group(1) or "").strip() == "담주":
                ahead += 7 if ahead <= (6 - today.weekday()) else 0
            d = today + timedelta(days=ahead)
            cut(m)

    # ── 시각 ──
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", s)
    if m and 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59:
        t = (int(m.group(1)), int(m.group(2)))
        cut(m)
    if t is None:
        # "오후 3시", "3시 반", "저녁 7시 30분" — '3시간'은 시각이 아니다
        m = re.search(r"(오전|오후|아침|저녁|밤|새벽)?\s*(\d{1,2})\s*시(?!\s*간)"
                      r"(?:\s*(반)|\s*(\d{1,2})\s*분)?", s)
        if m:
            h = int(m.group(2))
            mi = 30 if m.group(3) else int(m.group(4) or 0)
            ampm = m.group(1)
            if ampm in ("오후", "저녁", "밤") and h < 12:
                h += 12
            elif ampm is None and 1 <= h <= 6:
                # 맨 "3시"는 새벽 3시보다 오후 3시일 확률이 훨씬 높다(약속·마감)
                h += 12
            if 0 <= h <= 23 and 0 <= mi <= 59:
                t = (h, mi)
                cut(m)

    # 시각만 있고 날짜가 없으면 오늘 — 단 이미 지났으면 내일로 본다("3시 미팅"을 저녁에 적는 경우)
    if t is not None and d is None:
        cand = datetime.combine(today, datetime.min.time()).replace(hour=t[0], minute=t[1])
        d = today if cand > datetime.now() else today + timedelta(days=1)

    rest = re.sub(r"\s{2,}", " ", s)
    rest = re.sub(r"^\s*(까지|까지는|에|까지만)\s*", "", rest).strip()
    rest = re.sub(r"\s*(까지|까지는|까지만)\s*$", "", rest).strip()
    if not rest:
        rest = (text or "").strip()             # 날짜만 적었으면 원문을 남긴다

    if d is None:
        return None, rest
    if t is None:
        return d.isoformat(), rest
    return f"{d.isoformat()}T{t[0]:02d}:{t[1]:02d}", rest


def _mk_md(today, month, day, roll_month=False):
    """월·일 → 올해 날짜. 이미 지났으면 내년(또는 다음 달)."""
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d < today:
        if roll_month:
            m2 = month + 1
            y2 = today.year + (1 if m2 > 12 else 0)
            m2 = 1 if m2 > 12 else m2
            try:
                d = date(y2, m2, day)
            except ValueError:
                return None
        else:
            try:
                d = date(today.year + 1, month, day)
            except ValueError:
                return None
    return d


# ═══════════════════════════════════════════════════════════════
#  저장·조회
# ═══════════════════════════════════════════════════════════════
def add(content, tag=None, due=None):
    raw = (content or "").strip()
    if not raw:
        return {"ok": False, "msg": "뭘 해야 하는지 같이 적어줘. 예: 할일 RawCut 납품본 내일까지"}
    parsed_due, body = parse_when(raw)
    due = due or parsed_due
    body = body or raw
    tag = tag or guess_project(raw)             # 날짜 빼기 전 원문으로 추정(프로젝트명이 잘릴 일 없게)
    with _conn() as c:
        dup = c.execute("SELECT id FROM memory WHERE kind='todo' AND status='open' AND content=?",
                        (body,)).fetchone()
        if dup:
            return {"ok": True, "id": dup["id"], "msg": f"이미 열려 있는 할일이야 (#{dup['id']})."}
        now = _now()
        cur = c.execute("INSERT INTO memory(kind, content, status, created, updated, tag, due) "
                        "VALUES('todo', ?, 'open', ?, ?, ?, ?)", (body, now, now, tag, due))
        tid = cur.lastrowid
    export()
    return {"ok": True, "id": tid, "due": due, "tag": tag,
            "msg": f"할일 #{tid} 접수 [{tag}] {_due_label(due)} — {body}".replace("  ", " ")}


def _rows(status="open"):
    q = ("SELECT id, content, status, created, updated, tag, due FROM memory "
         "WHERE kind='todo'")
    args = ()
    if status != "all":
        q += " AND status=?"
        args = (status,)
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def _due_dt(due):
    """정렬용 — 마감 없는 것은 맨 뒤로."""
    if not due:
        return datetime.max
    try:
        return datetime.fromisoformat(due if "T" in due else due + "T23:59")
    except ValueError:
        return datetime.max


def _due_label(due, today=None):
    """'오늘 15:00' · 'D-2' · '지남 3일' · '—' 로 짧게."""
    if not due:
        return "—"
    today = today or date.today()
    try:
        d = date.fromisoformat(due[:10])
    except ValueError:
        return due
    clock = f" {due[11:16]}" if "T" in due else ""
    delta = (d - today).days
    if delta == 0:
        return f"오늘{clock}"
    if delta == 1:
        return f"내일{clock}"
    if delta < 0:
        return f"지남 {-delta}일{clock}"
    if delta <= 7:
        return f"D-{delta}{clock}"
    return f"{due[5:10].replace('-', '/')}{clock}"


def _line(r, today=None):
    return f"  #{r['id']:<3} {_due_label(r.get('due'), today):<12} {r['content']}"


def listing(status="open", project=None):
    """프로젝트별로 묶고, 묶음 안에서는 마감 임박순."""
    rows = _rows(status)
    if project:
        rows = [r for r in rows if (r.get("tag") or "기타").lower() == project.lower()]
    if not rows:
        if project:
            return f"[{project}] 열린 할일 없음."
        return "열린 할일 없음." if status == "open" else "할일 기록 없음."
    today = date.today()
    rows.sort(key=lambda r: _due_dt(r.get("due")))

    over = sum(1 for r in rows if r.get("due") and _due_dt(r["due"]).date() < today and r["status"] == "open")
    due_today = sum(1 for r in rows if r.get("due") and _due_dt(r["due"]).date() == today)
    week = sum(1 for r in rows
               if r.get("due") and today <= _due_dt(r["due"]).date() <= today + timedelta(days=7))
    head = f"할일 {len(rows)}건" if status == "open" else f"할일 전체 {len(rows)}건"
    marks = []
    if over:
        marks.append(f"지남 {over}")
    if due_today:
        marks.append(f"오늘 {due_today}")
    if week:
        marks.append(f"이번주 {week}")
    if marks:
        head += " (" + " · ".join(marks) + ")"

    groups = {}
    for r in rows:
        groups.setdefault(r.get("tag") or "기타", []).append(r)
    # 마감이 급한 프로젝트를 위로
    order = sorted(groups, key=lambda g: _due_dt(groups[g][0].get("due")))
    lines = [head]
    for g in order:
        lines.append(f"[{g}]")
        for r in groups[g]:
            mark = "" if r["status"] == "open" else "  (닫힘)"
            lines.append(_line(r, today) + mark)
    return "\n".join(lines)


def upcoming(days=0, only_timed=False, label=None):
    """오늘/이번주 보기 — 프로젝트로 안 묶고 시간순 한 줄기로.

    days=0 오늘까지 · days=7 이레 안. 지난 것은 항상 같이 보여준다(놓친 걸 숨기지 않게).
    only_timed=True 면 시각이 붙은 것만(= 약속·미팅).
    """
    today = date.today()
    end = today + timedelta(days=days)
    rows = []
    for r in _rows("open"):
        if not r.get("due"):
            continue
        if only_timed and "T" not in r["due"]:
            continue
        if _due_dt(r["due"]).date() <= end:
            rows.append(r)
    name = label or ("오늘" if days == 0 else f"{days}일 안")
    if not rows:
        return f"{name} 할 것 없음."
    rows.sort(key=lambda r: _due_dt(r.get("due")))
    lines = [f"{name} {len(rows)}건"]
    for r in rows:
        lines.append(f"  {_due_label(r['due'], today):<12} [{r.get('tag') or '기타'}] {r['content']}")
    return "\n".join(lines)


def export():
    """할일이 바뀔 때마다 `TODO.md` 로 내보낸다 — issue_box 가 ISSUES.md 를 뽑는 것과 같은 이유.

    클로드가 호윤 프로젝트를 손대기 전에 '지금 뭘 밀고 있는지'를 파일로 바로 보게 하는 용도.
    DB(sqlite)는 열어봐야 알지만 이건 폴더에 놓여 있다.
    ⚠️개인 일감이 그대로 적히는 파일이다 — 공개 저장소에 올리지 말 것(.gitignore 에 넣어둠).
    """
    try:
        today = date.today()
        rows = _rows("all")
        opened = sorted([r for r in rows if r["status"] == "open"],
                        key=lambda r: _due_dt(r.get("due")))
        closed = sorted([r for r in rows if r["status"] != "open"],
                        key=lambda r: r["updated"], reverse=True)[:30]
        urgent = [r for r in opened
                  if r.get("due") and _due_dt(r["due"]).date() <= today + timedelta(days=7)]

        out = ["# COMET 할일",
               "",
               "> 자동 생성 — `todo_box.py` 가 할일이 바뀔 때마다 덮어쓴다. 직접 고치지 말 것.",
               "> 호윤이 COMET 에 `할일 …` 로 던져둔 것. **프로젝트 손대기 전에 여기부터 보면",
               "> 지금 뭘 밀고 있는지 안다.**",
               "> 넣기: `할일 RawCut 납품본 내일 3시까지` · 닫기: `할일 완료 번호 [메모]`",
               ""]
        out += [f"## 급한 것 ({len(urgent)})", ""]
        out += [f"- **#{r['id']}** [{r.get('tag') or '기타'}] {r['content']} "
                f"— {_due_label(r.get('due'), today)}" for r in urgent] or ["- 없음"]

        out += ["", f"## 열린 것 ({len(opened)})", ""]
        if opened:
            groups = {}
            for r in opened:
                groups.setdefault(r.get("tag") or "기타", []).append(r)
            for g in sorted(groups, key=lambda g: _due_dt(groups[g][0].get("due"))):
                out.append(f"### {g}")
                out += [f"- #{r['id']} {r['content']}  _{_due_label(r.get('due'), today)}_"
                        for r in groups[g]]
                out.append("")
        else:
            out += ["- 없음", ""]

        out += [f"## 최근 닫힌 것 ({len(closed)})", ""]
        out += [f"- ~~#{r['id']}~~ [{r.get('tag') or '기타'}] {r['content']}  "
                f"_(닫음 {r['updated'][:10]})_" for r in closed] or ["- 없음"]

        with open(EXPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    except Exception:
        pass                                        # 내보내기 실패가 할일 기능을 막지 않게


def digest(today=None):
    """아침에 읊어줄 한 덩어리 — 지난 것 · 오늘 · 이레 안 순서.

    모델 안 거친다(GPU 0). 읊을 게 없으면 None 을 돌려준다 — 빈 알림을 매일 보내면
    알림 자체를 안 보게 되기 때문에, 보낼 것이 있을 때만 보낸다.
    """
    today = today or date.today()
    rows = [r for r in _rows("open") if r.get("due")]
    if not rows:
        return None
    rows.sort(key=lambda r: _due_dt(r.get("due")))
    over, now_, soon = [], [], []
    for r in rows:
        d = _due_dt(r["due"]).date()
        if d < today:
            over.append(r)
        elif d == today:
            now_.append(r)
        elif d <= today + timedelta(days=7):
            soon.append(r)
    if not (over or now_ or soon):
        return None

    def block(title, rs):
        if not rs:
            return []
        out = [title]
        for r in rs:
            clock = f"{r['due'][11:16]} " if "T" in r["due"] else ""
            mark = "" if title.startswith("오늘") else f"{_due_label(r['due'], today)} "
            out.append(f"  {clock or mark}[{r.get('tag') or '기타'}] {r['content']}")
        return out

    lines = [f"📌 {today.strftime('%m/%d')} 할 일"]
    lines += block(f"지난 것 {len(over)}", over)
    lines += block(f"오늘 {len(now_)}", now_)
    lines += block(f"이레 안 {len(soon)}", soon)
    rest = len([r for r in _rows("open") if not r.get("due")])
    if rest:
        lines.append(f"(마감 없는 것 {rest}건 — '할일' 로 전체 보기)")
    return "\n".join(lines)


def complete(key, note=""):
    """번호(#3) 또는 키워드로 닫는다."""
    with _conn() as c:
        row = None
        if re.fullmatch(r"#?\d+", str(key).strip()):
            iid = int(str(key).strip().lstrip("#"))
            row = c.execute("SELECT id, content, status FROM memory WHERE kind='todo' AND id=?",
                            (iid,)).fetchone()
            if not row:
                return {"ok": False, "msg": f"#{iid} 할일 없음."}
            if row["status"] != "open":
                return {"ok": False, "msg": f"#{iid} 는 이미 닫혀 있어."}
        else:
            for q in memory_db._match_candidates(str(key)):
                row = c.execute("SELECT id, content, status FROM memory WHERE kind='todo' "
                                "AND status='open' AND content LIKE ? ORDER BY updated DESC LIMIT 1",
                                (f"%{q}%",)).fetchone()
                if row:
                    break
            if not row:
                return {"ok": False, "msg": f"'{key}' 에 맞는 열린 할일 없음."}
        content = row["content"] + (f"  → {note.strip()}" if note.strip() else "")
        c.execute("UPDATE memory SET status='done', content=?, updated=? WHERE id=?",
                  (content, _now(), row["id"]))
    export()
    return {"ok": True, "id": row["id"], "msg": f"#{row['id']} 완료 — {content}"}


def reopen(iid):
    with _conn() as c:
        n = c.execute("UPDATE memory SET status='open', updated=? WHERE kind='todo' AND id=? "
                      "AND status!='open'", (_now(), iid)).rowcount
    export()
    return {"ok": bool(n), "msg": f"#{iid} 다시 열었어." if n else f"#{iid} 는 없거나 이미 열려 있어."}


# ═══════════════════════════════════════════════════════════════
#  명령 파서 — comet.respond 가 '할일…' / '일정…' 으로 시작하면 넘긴다
# ═══════════════════════════════════════════════════════════════
_PROJECT_NAMES = {p.lower(): p for p, _ in PROJECTS}
_PROJECT_NAMES["기타"] = "기타"


def command(text):
    """처리했으면 문자열, 이 모듈이 처리할 말이 아니면 None(→ 일반 대화로 흘림)."""
    t = (text or "").strip()
    head = re.match(r"^(할\s*일|할일|투두|todo|일정|스케줄)\s*", t, re.I)
    if not head:
        return None
    kw = head.group(1).replace(" ", "")
    body = t[head.end():].strip()
    flat = re.sub(r"[?!.]+$", "", body.replace(" ", ""))

    # 보기 ─────────────────────────────────────────────────────
    #  ※ 일반 질문 방어는 여기 아래(접수 직전)에 둔다 — 위에 두면 "할일 이번주"가
    #    공백 지운 "할일이번주"의 '이'를 조사로 오해해 대화로 새 나갔다(검증에서 잡힘).
    if kw in ("일정", "스케줄") and flat in ("", "목록", "보여줘", "뭐있어", "뭐있어?"):
        return upcoming(days=7, only_timed=True, label="시각 있는 일정(이레 안)")
    if flat in ("", "목록", "리스트", "보여줘", "뭐있어", "뭐있어?", "남은거", "뭐남았어"):
        return listing("open")
    if flat in ("전체", "전부", "다", "닫힌것", "지난것"):
        return listing("all")
    if flat in ("오늘", "오늘거", "오늘꺼", "오늘것", "today"):
        return upcoming(days=0, label="오늘")
    if flat in ("내일", "낼", "내일거", "내일꺼"):
        return upcoming(days=1, label="내일까지")
    if flat in ("이번주", "금주", "주간", "이번주거", "일주일"):
        return upcoming(days=7, label="이레 안")
    if flat.lower() in _PROJECT_NAMES:           # "할일 RawCut" = 그 프로젝트만
        return listing("open", project=_PROJECT_NAMES[flat.lower()])

    # 닫기·다시 열기 ────────────────────────────────────────────
    m = (re.match(r"^(?:완료|닫기|닫아|끝|끝냄|끝냈어|done)\s*(.+)$", body) or
         re.match(r"^(.+?)\s*(?:완료|닫기|닫아|끝냈어|끝냄|done)$", body))
    if m:
        key = m.group(1).strip()
        note = ""
        m2 = re.match(r"^(#?\d+)\s+(.*)$", key)
        if m2:
            key, note = m2.group(1), m2.group(2)
        return complete(key, note)["msg"]
    m = re.match(r"^(?:다시|열기|reopen)#?(\d+)$", flat) or re.match(r"^#?(\d+)다시(?:열기)?$", flat)
    if m:
        return reopen(int(m.group(1)))["msg"]

    # 일반 질문은 접수하지 않고 대화로 흘린다 ──────────────────────
    #  "할일이 뭐야?"(키워드에 조사가 붙음) · "일정 관리 어떻게 해?"(물음표)
    #  단 "할일 뭐 남았어?" 처럼 짧게 목록을 묻는 건 목록으로 답한다.
    if re.match(r"^(할\s*일|할일|일정|스케줄)(이|가|은|는|을|를|이란|란|라는|들)\s", t) or t.endswith("?"):
        if len(flat) <= 12 and re.search(r"뭐있|뭐남|몇개|남은|목록|보여", flat):
            return listing("open")
        return None

    # 나머지는 접수 ─────────────────────────────────────────────
    return add(body)["msg"]


if __name__ == "__main__":       # 빠른 확인: python todo_box.py
    print(listing("open"))
