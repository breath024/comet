# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 정세 로그 (marketlog)
#   목적: web.py·analyst·news.py 가 긁어온 사실을 그때 쓰고 버리지 않고
#         날짜 박힌 로그로 쌓아 '정세의 흐름'을 추적 + 분석에 되먹임.
#   원칙(호윤 지시): 모든 항목엔 접근가능한 출처(URL)가 있어야 한다.
#         중대사안(암살·전쟁·디폴트 등)인데 듣보/단독/특보+과장어로 '말로만 확신'
#         주려는 약한 출처는 가짜 의심으로 걸러낸다(=로그에 사실로 안 들임).
#   저장: SQLite(market_log.db). 중복방지=내용 해시 UNIQUE. 날짜=출처추정일/없으면 당일.
#   의존성 0(표준 sqlite3·hashlib만). web.py 의 출처등급(tier) 재사용.
# ═══════════════════════════════════════════════════════════════
import os
import re
import sqlite3
import hashlib
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_log.db")

# 중대사안 신호 — 사실이면 시장/사회 충격이 큰 주장(그래서 검증 문턱을 높인다)
_HIGH_IMPACT = ("암살", "피살", "사망", "사망설", "전쟁", "침공", "쿠데타", "계엄", "탄핵",
                "디폴트", "파산", "도산", "폭락", "폭등", "급락", "급등", "체포", "구속",
                "테러", "핵실험", "전면전", "리콜", "상장폐지", "횡령", "분식")
# 과장·'말로만 확신' 신호 — 단독/특보/충격성 어휘로 믿음을 강요하는 행보
_HYPE = ("[단독]", "단독", "속보", "특보", "충격", "경악", "발칵", "초유", "긴급",
         "breaking", "exclusive", "must", "shocking")


def _today():
    return datetime.date.today().isoformat()


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _hash(topic, text):
    return hashlib.sha1((_norm(topic) + "|" + _norm(text)[:200]).encode("utf-8")).hexdigest()


def _has(text, words):
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS market_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, topic TEXT, title TEXT, fact TEXT,
        url TEXT, domain TEXT, tier INTEGER, lang TEXT,
        magnitude TEXT, status TEXT, reason TEXT,
        hash TEXT UNIQUE, created TEXT)""")
    # 기존 DB에 lang 칼럼이 없으면 추가(마이그레이션, 멱등)
    try:
        c.execute("ALTER TABLE market_log ADD COLUMN lang TEXT")
    except sqlite3.OperationalError:
        pass
    return c


# ── 신뢰 판정 = '접근가능 출처 + 출처강도 + 사안중대성 + 교차검증' ───
#   status: confirmed(사실로 채택) / unconfirmed(참고·미확인 표시) / rejected(가짜 의심·안 들임)
def assess(title, snippet, url, tier, root_ref, strong_count):
    """strong_count = 같은 수집 묶음에서 등급<=2(원천/보도) 도메인 수 = 교차검증 대용."""
    text = f"{title} {snippet}"
    if not (url or "").startswith("http"):
        return ("rejected", "출처 링크 없음(접근 불가)")
    high = _has(text, _HIGH_IMPACT)
    hype = _has(text, _HYPE)
    if high:
        # 중대사안: 등급 높고(원천/보도) 원천인용 or 2곳 이상 교차될 때만 사실로
        if tier <= 2 and (root_ref or strong_count >= 2):
            return ("confirmed", f"중대사안·등급{tier}·{'원천인용' if root_ref else '교차확인'}")
        if tier >= 3 or hype:
            return ("rejected",
                    f"중대사안인데 약한출처(등급{tier}{'·과장어' if hype else ''}, 단독/미확인) → 가짜 의심, 걸러냄")
        return ("unconfirmed", f"중대사안·확인부족(등급{tier})")
    # 일반 사안
    if tier <= 2:
        return ("confirmed", f"등급{tier}")
    if hype:
        return ("rejected", f"과장어+등급{tier} 약한출처 → 신뢰 낮아 걸러냄")
    return ("unconfirmed", f"등급{tier} 참고(미확인)")


# ── 쓰기: web.research() 결과(sources)를 토픽으로 적재 ───────────
def log_research(topic, research, date_hint=None):
    """research = web.research() 반환 dict. 각 source를 신뢰판정 후 confirmed/unconfirmed만 저장.
       반환 {logged, dup, rejected, rejects:[reason...]}."""
    if not research or not research.get("ok"):
        return {"logged": 0, "dup": 0, "rejected": 0, "rejects": []}
    srcs = research.get("sources", [])
    strong = sum(1 for s in srcs if s.get("tier", 3) <= 2)
    out = {"logged": 0, "dup": 0, "rejected": 0, "rejects": []}
    c = _conn()
    for s in srcs:
        title = s.get("title", "")
        snippet = (s.get("snippet") or s.get("text", "") or "")[:280]
        url = s.get("url", "")
        tier = s.get("tier", 3)
        status, reason = assess(title, snippet, url, tier, s.get("root_ref", False), strong)
        if status == "rejected":
            out["rejected"] += 1
            out["rejects"].append(f"{reason} — {title[:40]}")
            continue
        h = _hash(topic, title + snippet)
        date = s.get("date") or date_hint or _today()
        try:
            c.execute("""INSERT INTO market_log
                (date, topic, title, fact, url, domain, tier, lang, magnitude, status, reason, hash, created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (date, topic, title, snippet, url, s.get("domain", ""), tier, s.get("lang"),
                 "high" if _has(title + snippet, _HIGH_IMPACT) else "normal",
                 status, reason, h, _now()))
            out["logged"] += 1
        except sqlite3.IntegrityError:
            out["dup"] += 1
    c.commit()
    c.close()
    return out


# ── 쓰기: news.py 수집 기사를 토픽으로 적재(신뢰 필터 동일 적용) ──
def log_news(items, topic=None):
    """news.collect()의 new_items를 정세 로그에 적재. 출처등급은 도메인으로 산정.
       반환 {logged, dup, rejected, rejects}."""
    out = {"logged": 0, "dup": 0, "rejected": 0, "rejects": []}
    if not items:
        return out
    try:
        import web
        from email.utils import parsedate_to_datetime
    except Exception:
        return out
    c = _conn()
    for it in items:
        url = it.get("link", "")
        dom = web._domain(url) if url else ""
        tier = web._tier(dom) if dom else 3
        title = it.get("title", "")
        snip = (it.get("summary", "") or "")[:280]
        # 뉴스는 단일 출처 → strong_count=1(중대사안+약한출처면 가짜 의심으로 걸러짐)
        status, reason = assess(title, snip, url, tier, False, 1)
        if status == "rejected":
            out["rejected"] += 1
            out["rejects"].append(f"{reason} — {title[:40]}")
            continue
        try:
            date = parsedate_to_datetime(it.get("pub", "")).date().isoformat()
        except Exception:
            date = _today()
        tp = topic or it.get("keyword", "뉴스")
        h = _hash(tp, title + snip)
        try:
            c.execute("""INSERT INTO market_log
                (date, topic, title, fact, url, domain, tier, lang, magnitude, status, reason, hash, created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (date, tp, title, snip, url, dom, tier, it.get("lang"),
                 "high" if _has(title + snip, _HIGH_IMPACT) else "normal",
                 status, reason, h, _now()))
            out["logged"] += 1
        except sqlite3.IntegrityError:
            out["dup"] += 1
    c.commit()
    c.close()
    return out


# ── 읽기: 토픽 타임라인(흐름) ────────────────────────────────────
def _alias_terms(topic):
    """'엔비디아'↔'NVDA'처럼 같은 종목의 한글명/티커를 함께 검색하도록 별칭 확장."""
    terms = {_norm(topic)}
    try:
        from analyst import NAME2TICKER   # 지연 임포트(순환 회피)
        ql = _norm(topic)
        for name, tk in NAME2TICKER.items():
            if name in ql:
                terms.add(_norm(tk))      # 한글명 → 티커
            if _norm(tk) == ql:
                terms.add(_norm(name))    # 티커 → 한글명
    except Exception:
        pass
    return [t for t in terms if t]


def recent(topic, days=60, limit=40):
    c = _conn()
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    terms = _alias_terms(topic)
    cond = " OR ".join(["(lower(topic) LIKE ? OR lower(title) LIKE ?)"] * len(terms))
    params = []
    for t in terms:
        params += [f"%{t}%", f"%{t}%"]
    params += [since, limit]
    rows = c.execute(
        f"""SELECT date, title, url, domain, tier, status, lang FROM market_log
            WHERE ({cond}) AND date >= ?
            ORDER BY date DESC, id DESC LIMIT ?""", params).fetchall()
    c.close()
    return rows


def situation(topic, days=60):
    """토픽 정세를 날짜순(최신 위) 타임라인 텍스트로. 흐름 읽기 + 링크 보존."""
    rows = recent(topic, days=days)
    if not rows:
        return {"ok": False, "msg": f"'{topic}' 정세 로그가 아직 비어 있어. 분석/검색을 돌리면 쌓이기 시작해."}
    by_date = {}
    for date, title, url, domain, tier, status, lang in rows:
        by_date.setdefault(date, []).append((title, url, domain, tier, status, lang))
    lines = [f"📜 '{topic}' 정세 흐름 (최근 {days}일, 최신 위 · 총 {len(rows)}건)"]
    for date in sorted(by_date, reverse=True):
        lines.append(f"\n■ {date}")
        for title, url, domain, tier, status, lang in by_date[date]:
            mark = "✓" if status == "confirmed" else "?"
            ctry = f"·{lang}" if lang else ""
            lines.append(f"  {mark} [{domain}·등급{tier}{ctry}] {title[:70]}\n     {url}")
    return {"ok": True, "topic": topic, "count": len(rows), "text": "\n".join(lines)}


def stats():
    c = _conn()
    n = c.execute("SELECT COUNT(*) FROM market_log").fetchone()[0]
    topics = c.execute(
        "SELECT topic, COUNT(*) c FROM market_log GROUP BY topic ORDER BY c DESC LIMIT 12").fetchall()
    c.close()
    return {"ok": True, "total": n, "topics": topics}


def dispatch(action, args):
    if action == "market_log":
        return situation(args.get("query") or args.get("topic", ""),
                         days=int(args.get("days", 60)))
    return {"ok": False, "msg": f"알 수 없는 정세로그 동작: {action}"}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    import web
    topic = " ".join(sys.argv[1:]) or "반도체"
    print(f"수집→적재: {topic}")
    r = web.research(topic + " 전망", n_sources=5)
    res = log_research(topic, r)
    print("적재:", res)
    print()
    print(situation(topic).get("text") or situation(topic).get("msg"))
    print("\n전체:", stats())
