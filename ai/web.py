# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 웹 검색 엔진 (DuckDuckGo lite, API 키 0, 의존성 0)
#   검색 → 상위 페이지 본문 긁기 → COMET LLM 이 출처 인용해 답하도록 자료만 제공.
#   숫자/판단/요약은 안 함. 검색·본문추출까지만. 근거 위 추론은 위(comet.py)에서.
#   설계 원칙: news.py 와 동일 — urllib 표준라이브러리만, 결과는 {ok, ...} dict.
# ═══════════════════════════════════════════════════════════════
import os
import re
import json
import html
import gzip
import datetime
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) COMET/1.0"
SEARCH_URL = "https://lite.duckduckgo.com/lite/"

# 본문에서 통째로 들어내는 태그(스크립트/스타일/메뉴 등 노이즈)
_DROP_BLOCK = re.compile(
    r"<(script|style|noscript|template|svg|head|nav|header|footer|aside)\b.*?</\1>",
    re.I | re.S,
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANK = re.compile(r"\n\s*\n\s*\n+")


# ── 공통 HTTP GET (gzip/charset 처리) ────────────────────────────
def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ko,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                pass
        # charset 추정 (헤더 → utf-8 폴백)
        ctype = r.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ctype, re.I)
        enc = m.group(1) if m else "utf-8"
    try:
        return raw.decode(enc, "ignore")
    except Exception:
        return raw.decode("utf-8", "ignore")


def _unwrap(href):
    """DuckDuckGo 리다이렉트(//duckduckgo.com/l/?uddg=...) → 실제 URL."""
    m = re.search(r"uddg=([^&]+)", href)
    if m:
        return urllib.parse.unquote(m.group(1))
    return href


def _clean(s):
    return _WS.sub(" ", html.unescape(_TAG.sub("", s or ""))).strip()


# ── 출처 분류 = '뿌리(원천)에 얼마나 가까운가' (매체 종류로 신뢰를 매기지 않는다) ──
#   1 = 원천·공식 : 그 사실을 '만든' 주체 자체(정부·통계·중앙은행·기업공식·법령·1차문서)
#   2 = 보도      : 언론이 원천을 '전달'. 매체라서 믿는 게 아니라 원천을 추적할 수 있을 때만 신뢰.
#   3 = 참고      : 블로그·카페·위키·개인. 믿지 말고 '단서/참고'로만. 항상 미확인 취급.
#   ※ 위키·나무위키는 누구나 편집 → 원천 아님(참고). 한국·해외 모두 가짜·왜곡 가능 전제.
_TIER1 = ("go.kr", ".gov", "gov.", "bok.or.kr", "kostat.go.kr", "fss.or.kr", "fsc.go.kr",
          "krx.co.kr", "law.go.kr", ".ac.kr", ".edu", "who.int", "un.org", "imf.org",
          "oecd.org", "worldbank.org", "data.go.kr", "moef.go.kr", "molit.go.kr")
_TIER2 = ("yna.co.kr", "yonhapnews", "chosun.com", "donga.com", "joongang", "joins.com",
          "hani.co.kr", "khan.co.kr", "mk.co.kr", "hankyung.com", "sedaily.com", "mt.co.kr",
          "kbs.co.kr", "imbc.com", "sbs.co.kr", "ytn.co.kr", "newsis.com", "edaily.co.kr",
          "reuters.com", "bloomberg.com", "apnews.com", "bbc.", "nytimes.com", "wsj.com",
          "ft.com", "cnbc.com", "investing.com", "coindesk.com", "theguardian.com")
# 참고(3)로 확정해야 하는 곳 — 위키·블로그·카페·개인
_TIER3 = ("wikipedia.org", "namu.wiki", "tistory.com", "blog.naver", "blog.daum",
          "brunch.co.kr", "cafe.naver", "cafe.daum", "post.naver", "velog.io", "medium.com")
_TIER_LABEL = {1: "원천·공식", 2: "보도(언론)", 3: "참고(미확인)"}

# 본문이 '뿌리'를 인용하는지 단서 — 원천 추적 가능성(매체라도 원천 대면 신뢰↑)
_ROOT_TERMS = ("한국은행", "통계청", "기획재정부", "금융위", "금융감독원", "발표", "공시",
               "보도자료", "에 따르면", "밝혔다", "공식", "정부는", "관계자", "원문",
               "according to", "said in a statement", "official", "data showed", "reported by")


def _domain(url):
    try:
        net = urllib.parse.urlparse(url).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return ""


def _tier(domain):
    if any(t in domain for t in _TIER3):   # 위키·블로그·카페는 출처가 무엇이든 참고(3)
        return 3
    if any(t in domain for t in _TIER1):
        return 1
    if any(t in domain for t in _TIER2):
        return 2
    return 3   # 미상도 기본 참고(3) — 모르는 곳을 믿지 않는다


def _root_hint(text):
    """본문이 원천(한국은행·발표·통계 등)을 인용/언급하는지 — 뿌리 추적 단서."""
    low = (text or "").lower()
    return any(t.lower() in low for t in _ROOT_TERMS)


# 날짜 추출 — 페이지 텍스트에서 가장 최근(미래 아님) 날짜를 추정
_DATE_PATS = [
    (re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})"), (0, 1, 2)),
    (re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})"), (0, 1, 2)),
]
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_EN_DATE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", re.I)


def _extract_date(text):
    today = datetime.date.today()
    best = None
    head = text[:1500]   # 최신 게시일은 보통 본문 앞쪽
    for pat, (yi, mi, di) in _DATE_PATS:
        for m in pat.finditer(head):
            try:
                d = datetime.date(int(m.group(yi + 1)), int(m.group(mi + 1)), int(m.group(di + 1)))
                if d <= today and (best is None or d > best):
                    best = d
            except Exception:
                continue
    for m in _EN_DATE.finditer(head):
        try:
            d = datetime.date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
            if d <= today and (best is None or d > best):
                best = d
        except Exception:
            continue
    return best.isoformat() if best else None


# ── 1) 검색 ──────────────────────────────────────────────────────
# DuckDuckGo lite 가 봇차단 챌린지 페이지를 돌려줄 때 뜨는 문구 — "결과 0개"와 구분하려고 감지.
_DDG_BLOCK_MARKERS = ("bots use duckduckgo too", "unusual traffic", "verify you are a human")

# Brave Search API — 키 있을 때만 승급(무료 2000건/월, api.search.brave.com 에서 발급).
# llm.py 의 "로컬 무료 우선 → 키 있으면 API 승급"과 같은 패턴: DDG 가 막혔을 때만 쓴다.
BRAVE_API_KEY_ENV = "BRAVE_API_KEY"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


def _ddg_search(query, n, kl):
    """DuckDuckGo lite 원본 로직. 성공하면 dict, 실패/차단이면 {ok:False,...}."""
    try:
        q = urllib.parse.urlencode({"q": query, "kl": kl or "wt-wt"})
        raw = _get(SEARCH_URL + "?" + q)
    except Exception as e:
        return {"ok": False, "msg": f"검색 실패: {e}"}

    if any(m in raw.lower() for m in _DDG_BLOCK_MARKERS):
        return {"ok": False, "msg": "DuckDuckGo 봇차단(임시) — 결과가 없는 게 아니라 막힌 것.", "blocked": True}

    links = re.findall(
        r"<a[^>]*href=\"([^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>",
        raw, re.I | re.S,
    )
    snippets = re.findall(
        r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
        raw, re.I | re.S,
    )
    results = []
    for i, (href, title) in enumerate(links):
        url = _unwrap(href)
        if not url.startswith("http"):
            continue
        snip = _clean(snippets[i]) if i < len(snippets) else ""
        results.append({"title": _clean(title), "url": url, "snippet": snip[:300]})
        if len(results) >= n:
            break

    if not results:
        return {"ok": False, "msg": f"'{query}' 검색 결과를 못 찾았어."}
    return {"ok": True, "query": query, "results": results}


def _brave_search(query, n, kl):
    """Brave Search API. 키 없으면 None(승급 불가), 있으면 dict."""
    key = os.environ.get(BRAVE_API_KEY_ENV, "").strip()
    if not key:
        return None
    try:
        country = "KR" if (kl or "").startswith("kr") else "US"
        q = urllib.parse.urlencode({"q": query, "count": n, "country": country})
        req = urllib.request.Request(
            BRAVE_URL + "?" + q,
            headers={"Accept": "application/json", "X-Subscription-Token": key},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "msg": f"Brave 검색 실패: {e}"}

    results = [
        {"title": _clean(item.get("title", "")), "url": item.get("url", ""),
         "snippet": _clean(item.get("description", ""))[:300]}
        for item in (data.get("web", {}) or {}).get("results", [])[:n]
    ]
    if not results:
        return {"ok": False, "msg": f"'{query}' Brave 검색 결과를 못 찾았어."}
    return {"ok": True, "query": query, "results": results}


def search(query, n=6, kl="kr-ko"):
    """DuckDuckGo lite 검색(키 불필요) → 막히면 Brave 로 승급(BRAVE_API_KEY 있을 때만).
       kl = 지역·언어 코드(kr-ko/us-en/cn-zh/jp-jp/de-de…) — 나라별 현지 결과를 받기 위함."""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "msg": "검색어가 비었어."}

    ddg = _ddg_search(query, n, kl)
    if ddg.get("ok"):
        return ddg

    brave = _brave_search(query, n, kl)
    if brave is not None:
        return brave
    return ddg


# ── 2) 페이지 본문 추출 ──────────────────────────────────────────
def fetch(url, max_chars=2800):
    """단일 페이지 → 사람이 읽을 본문 텍스트(노이즈 제거, 길이 제한)."""
    try:
        raw = _get(url, timeout=12)
    except Exception as e:
        return {"ok": False, "url": url, "msg": f"읽기 실패: {e}"}
    body = _DROP_BLOCK.sub(" ", raw)
    # <br>,</p>,</div> 등은 줄바꿈으로 보존 후 태그 제거
    body = re.sub(r"<(br|/p|/div|/li|/h[1-6])\b[^>]*>", "\n", body, flags=re.I)
    text = html.unescape(_TAG.sub("", body))
    text = _WS.sub(" ", text)
    text = _BLANK.sub("\n\n", text)
    text = "\n".join(ln.strip() for ln in text.splitlines() if ln.strip())
    text = text.strip()
    if not text:
        return {"ok": False, "url": url, "msg": "본문이 비어 있음(JS 렌더 페이지일 수 있음)."}
    return {"ok": True, "url": url, "text": text[:max_chars],
            "truncated": len(text) > max_chars}


# ── 3) 리서치: 정형화된 수집 — 질(신뢰등급·최신성·교차검증)로 줄세움 ──
def research(query, n_sources=4, per_chars=2800, kl="kr-ko", lang=None):
    """검색 → 도메인 중복 제거 → 신뢰등급순 정렬 → 상위만 본문 확보 →
       각 출처에 (도메인·신뢰등급·추정날짜·언어태그) 메타 부착.
       kl=지역·언어 코드, lang=출처에 박을 국가/언어 라벨(다국어 수집 시 어느 나라 자료인지)."""
    s = search(query, n=12, kl=kl)   # 후보는 넉넉히(고르기 위함), 최종은 질로 추림
    if not s.get("ok"):
        return s

    # 1) 도메인당 1개만 — 같은 블로그/사이트가 머릿수로 압도하지 못하게
    by_domain = {}
    for r in s["results"]:
        dom = _domain(r["url"])
        if not dom or dom in by_domain:
            continue
        r["domain"], r["tier"] = dom, _tier(dom)
        by_domain[dom] = r
    # 2) 신뢰등급 우선 정렬(같은 등급은 검색 순위 유지)
    candidates = sorted(by_domain.values(), key=lambda x: x["tier"])

    # 3) 등급 좋은 것부터 본문 확보(읽히는 것 위주), 목표 개수까지
    sources, used = [], 0
    for r in candidates:
        if used >= n_sources:
            break
        page = fetch(r["url"], max_chars=per_chars)
        if page.get("ok"):
            text, readable = page["text"], True
        elif r["snippet"]:
            text, readable = "(본문 못 읽음 — 검색 요약만) " + r["snippet"], False
        else:
            continue
        used += 1
        sources.append({
            "n": used, "title": r["title"], "url": r["url"],
            "domain": r["domain"], "tier": r["tier"],
            "tier_label": _TIER_LABEL[r["tier"]],
            "date": _extract_date(text) if readable else None,
            "root_ref": _root_hint(text) if readable else False,
            "lang": lang,
            "readable": readable, "snippet": r["snippet"], "text": text,
        })

    if not sources:
        return {"ok": False, "query": query,
                "msg": "검색은 됐지만 어느 페이지도 본문을 못 읽었어."}
    # 교차검증 판단용 요약 메타
    tiers = [s["tier"] for s in sources]
    return {"ok": True, "query": query, "count": len(sources),
            "domains": len({s["domain"] for s in sources}),
            "best_tier": min(tiers), "sources": sources}


# ── 도구 디스패치 (memory_db/files/projects 와 동일 규약) ─────────
def dispatch(action, args):
    if action == "web_search":
        return research(args.get("query", ""))
    return {"ok": False, "msg": f"알 수 없는 웹 동작: {action}"}


if __name__ == "__main__":
    import sys, io, json
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "서울 부산 KTX 소요시간 요금"
    print("== search ==")
    s = search(q, 5)
    for r in s.get("results", []):
        print("-", r["title"][:60], "\n   ", r["url"])
    print("\n== research ==")
    r = research(q, n_sources=4)
    print("ok:", r.get("ok"), "count:", r.get("count"),
          "domains:", r.get("domains"), "best_tier:", r.get("best_tier"))
    for src in r.get("sources", []):
        print(f"\n[{src['n']}] (등급{src['tier']}:{src['tier_label']} · {src['domain']}"
              f" · 날짜 {src.get('date') or '미상'})\n{src['title'][:60]}\n{src['url']}"
              f"\n{src['text'][:160]}...")
