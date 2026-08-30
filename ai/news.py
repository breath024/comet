# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 뉴스 감시 엔진 (구글 뉴스 RSS, API 키 0, 의존성 0)
#   watchlist의 키워드별로 한국/해외 기사를 긁어 "신규"만 골라낸다.
#   트리거어(판결·선고·소송 등)가 걸린 신규는 '긴급'으로 표시 → 즉시 푸시용.
#   숫자/판단은 안 함. 수집·중복제거·분류까지만. 정리(요약)는 COMET LLM이 위에서.
# ═══════════════════════════════════════════════════════════════
import os
import re
import json
import time
import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
WATCH_FILE = os.path.join(_DIR, "watchlist.json")
SEEN_FILE  = os.path.join(_DIR, "news_seen.json")
SEEN_MAX   = 4000          # 본 기사 식별자 보관 상한(오래된 것부터 버림)
UA = "Mozilla/5.0 (COMET news watcher)"

# 언어별 구글뉴스 파라미터
_LANG = {
    "ko": {"hl": "ko",    "gl": "KR", "ceid": "KR:ko"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}

# 키워드에 트리거를 안 적었을 때 언어별 기본 트리거(법정/제재 이벤트)
DEFAULT_TRIGGERS = {
    "ko": ["판결", "선고", "구속", "기소", "고소", "고발", "소송", "제재", "압수수색",
           "벌금", "과징금", "리콜", "상장폐지", "거래정지", "유상증자", "감자"],
    "en": ["ruling", "verdict", "lawsuit", "settlement", "indicted", "subpoena",
           "sanction", "fine", "recall", "delist", "bankruptcy", "injunction"],
}


# ── 본 기사 기록 (중복 알림 방지) ────────────────────────────────
def _load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []          # 순서 보존 리스트(앞=오래됨)


def _save_seen(seen):
    seen = seen[-SEEN_MAX:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)


def _strip(s):
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def _pub_ts(s):
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return 0.0


# ── 단일 키워드 수집 ─────────────────────────────────────────────
def fetch_keyword(keyword, lang="ko", limit=20, display=None):
    cfg = _LANG.get(lang, _LANG["ko"])
    q = urllib.parse.urlencode({"q": keyword, "hl": cfg["hl"],
                                "gl": cfg["gl"], "ceid": cfg["ceid"]})
    url = "https://news.google.com/rss/search?" + q
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=15).read()
    root = ET.fromstring(raw)
    out = []
    for it in root.findall(".//item")[:limit]:
        src = it.find("{*}source")
        out.append({
            "keyword": display or keyword,
            "lang": lang,
            "title": _strip(it.findtext("title", "")),
            "link": (it.findtext("link", "") or "").strip(),
            "guid": (it.findtext("guid", "") or it.findtext("link", "") or "").strip(),
            "source": (src.text if src is not None else "").strip(),
            "pub": it.findtext("pubDate", "")[:25],
            "_ts": _pub_ts(it.findtext("pubDate", "")),
            "summary": _strip(it.findtext("description", ""))[:300],
        })
    return out


def _triggers_for(entry):
    t = entry.get("triggers")
    if t:
        return t
    return DEFAULT_TRIGGERS.get(entry.get("lang", "ko"), [])


def _trigger_hit(item, triggers):
    hay = (item["title"] + " " + item["summary"]).lower()
    for t in triggers:
        if t.lower() in hay:
            return t
    return ""


def _legal_query(entry):
    """'키워드 (판결 OR 소송 OR 선고 …)' — 법정/제재 이벤트를 능동적으로 탐색."""
    trigs = _triggers_for(entry)[:8]      # 쿼리 과대 방지로 상위 8개
    if not trigs:
        return None
    return f'{entry["keyword"]} (' + " OR ".join(trigs) + ")"


# ── watchlist 전체 수집 → 신규/긴급 분류 ─────────────────────────
def collect(mark_only=False, per_keyword=20, log=True):
    """watchlist의 모든 키워드 수집. 신규만 반환, 트리거 걸린 건 urgent=True.
       mark_only=True: 알림 없이 현재 기사들을 '본 것'으로만 등록(최초 1회 베이스라인).
       log=True: 신규 기사를 정세 로그(marketlog)에도 자동 적재(가짜뉴스 필터·중복방지 적용)."""
    watch = load_watchlist()
    if not watch.get("items"):
        return {"ok": False, "msg": "watchlist가 비어 있음. add_watch로 키워드 등록 먼저."}

    seen = _load_seen()
    seen_set = set(seen)
    new_items, errors = [], []

    def _ingest(items, force_urgent, triggers):
        for it in items:
            gid = it["guid"]
            if not gid or gid in seen_set:
                continue
            seen_set.add(gid)
            seen.append(gid)
            if mark_only:
                continue
            hit = _trigger_hit(it, triggers)
            it["urgent"] = force_urgent or bool(hit)
            it["trigger"] = hit or ("법정검색" if force_urgent else "")
            new_items.append(it)

    for entry in watch["items"]:
        kw = entry.get("keyword", "").strip()
        if not kw:
            continue
        lang = entry.get("lang", "ko")
        triggers = _triggers_for(entry)
        # 1) 법정/제재 이벤트 우선(능동 탐색) → 전부 긴급
        lq = _legal_query(entry)
        if lq:
            try:
                _ingest(fetch_keyword(lq, lang, 12, display=kw), True, triggers)
            except Exception as e:
                errors.append(f"{kw}(법정): {e}")
        # 2) 일반 동향 → 트리거 걸리면 긴급, 아니면 일반
        try:
            _ingest(fetch_keyword(kw, lang, per_keyword), False, triggers)
        except Exception as e:
            errors.append(f"{kw}: {e}")

    _save_seen(seen)
    new_items.sort(key=lambda x: x["_ts"], reverse=True)
    urgent = [x for x in new_items if x.get("urgent")]
    # 정세 로그 자동 적재(신규만, 베이스라인 제외) — 뉴스가 날짜별로 누적돼 흐름 추적
    logged = None
    if log and not mark_only and new_items:
        try:
            import marketlog
            logged = marketlog.log_news(new_items)
        except Exception:
            logged = None
    return {"ok": True, "fetched_keywords": len(watch["items"]),
            "new": new_items, "urgent": urgent, "logged": logged,
            "errors": errors, "baseline": mark_only}


# ── watchlist 관리 ───────────────────────────────────────────────
def load_watchlist():
    try:
        with open(WATCH_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}


def save_watchlist(watch):
    with open(WATCH_FILE, "w", encoding="utf-8") as f:
        json.dump(watch, f, ensure_ascii=False, indent=2)


def add_watch(keyword, lang="ko", triggers=None) -> dict:
    keyword = (keyword or "").strip()
    if not keyword:
        return {"ok": False, "msg": "키워드 필요"}
    watch = load_watchlist()
    for e in watch["items"]:
        if e.get("keyword") == keyword and e.get("lang", "ko") == lang:
            return {"ok": False, "msg": f"이미 등록됨: {keyword}({lang})"}
    entry = {"keyword": keyword, "lang": lang}
    if triggers:
        entry["triggers"] = triggers
    watch.setdefault("items", []).append(entry)
    save_watchlist(watch)
    return {"ok": True, "msg": f"감시 추가: {keyword}({lang})", "count": len(watch["items"])}


def remove_watch(keyword) -> dict:
    keyword = (keyword or "").strip()
    watch = load_watchlist()
    before = len(watch.get("items", []))
    watch["items"] = [e for e in watch.get("items", []) if e.get("keyword") != keyword]
    save_watchlist(watch)
    removed = before - len(watch["items"])
    return {"ok": removed > 0, "msg": f"{keyword} {removed}건 제거" if removed else f"{keyword} 없음"}


def list_watch() -> dict:
    watch = load_watchlist()
    return {"ok": True, "items": watch.get("items", []), "count": len(watch.get("items", []))}


if __name__ == "__main__":
    # 빠른 수동 점검: 키워드 2개 등록 → 베이스라인 → 다시 수집
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print(add_watch("엔비디아", "ko"))
    print(add_watch("Nvidia", "en"))
    print("baseline:", {k: v for k, v in collect(mark_only=True).items() if k != "new"})
    r = collect()
    print("2차 수집 신규:", len(r["new"]), "긴급:", len(r["urgent"]))
