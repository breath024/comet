# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 금융 뉴스 소스 (Only Money 엔진과 동일 피드, 키 0·의존성 0)
#
#  왜: analyst 가 덕덕고(web.py)로 "엔비디아 사도될까"를 검색하면
#      티스토리·블로그(등급3)만 긁혀 컨센서스가 부실했다. Only Money
#      대시보드는 진짜 금융 매체 RSS(Yahoo 종목별·CNBC·MarketWatch·
#      Investing)에서 사건을 받는다 — 그 소스 목록을 그대로 가져와
#      종목/테마 분석의 1차 근거로 쓴다.
#
#  반환 형태는 web.research 의 source dict 와 똑같이 맞춰서
#  analyst._src_block / _sources_footer / _evidence 가 무수정 소비한다.
#  { n, tier, tier_label, domain, lang, date, root_ref, title, text, url }
#
#  이미지 역검색(imagesearch.py)은 web.py 를 '이게 뭔 사진인지' 식별
#  뒤 일반검색으로만 쓰므로 그대로 둔다 — 금융 소스는 여기로 분리.
# ═══════════════════════════════════════════════════════════════
import re
import html
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# Only Money index.html 이 쓰는 그 피드들. (종목별은 티커를 끼워 만든다)
TICKER_FEED = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US"
MACRO_FEEDS = [
    ("CNBC",        "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("Investing",   "https://www.investing.com/rss/news_25.rss"),
]

FRESH_DAYS = 30          # 이보다 오래된 기사는 버림(묵은 사건 차단)
MIN_KEEP   = 4           # 신선 필터로 너무 적게 남으면 최신순으로 이만큼은 살림


def _strip(s):
    return re.sub(r"<[^>]+>", "", html.unescape(s or "")).strip()


def _domain(url):
    try:
        d = urllib.parse.urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def _ts(s):
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        return 0.0


def _date(s):
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _fetch(url, limit=20):
    """RSS 한 개 → [{title, url, text, date, _ts, domain}] (실패 시 [])."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=12).read()
        root = ET.fromstring(raw)
    except Exception:
        return []
    out = []
    for it in root.findall(".//item")[:limit]:
        link = (it.findtext("link", "") or "").strip()
        title = _strip(it.findtext("title", ""))
        if not title or not link:
            continue
        pub = it.findtext("pubDate", "") or ""
        out.append({
            "title": title,
            "url": link,
            "text": _strip(it.findtext("description", ""))[:400],
            "date": _date(pub),
            "_ts": _ts(pub),
            "domain": _domain(link),
        })
    return out


def _fresh(items):
    """최근 FRESH_DAYS 만 남기되, 너무 적으면 최신순 MIN_KEEP 은 살린다."""
    now = datetime.now(timezone.utc).timestamp()
    cut = now - FRESH_DAYS * 86400
    items = sorted(items, key=lambda x: x["_ts"], reverse=True)
    fresh = [x for x in items if x["_ts"] >= cut]
    return fresh if len(fresh) >= MIN_KEEP else items[:MIN_KEEP]


def _as_sources(items, n_max, per_domain=3):
    """analyst source dict 형태로 변환(tier2=보도언론). 양질 단일 피드라
       블로그식 1도메인=1건 대신 도메인당 per_domain 까지 허용(근거 양 확보)."""
    cnt, out = {}, []
    for it in items:
        d = it["domain"]
        if cnt.get(d, 0) >= per_domain:
            continue
        cnt[d] = cnt.get(d, 0) + 1
        out.append({
            "n": len(out) + 1,
            "tier": 2,
            "tier_label": "보도언론",
            "domain": d,
            "lang": "미국/영어권",
            "date": it["date"],
            "root_ref": False,
            "title": it["title"],
            "text": it["text"],
            "url": it["url"],
        })
        if len(out) >= n_max:
            break
    return out


def for_ticker(ticker, n_max=6):
    """종목별 Yahoo Finance RSS → 그 종목 직접 기사(있으면 가장 강한 근거)."""
    items = _fetch(TICKER_FEED.format(t=urllib.parse.quote(ticker)))
    return _as_sources(_fresh(items), n_max)


def macro(n_max=6, per_feed=8):
    """티커 없는 테마/거시 질문용 — CNBC·MarketWatch·Investing 묶음."""
    pool = []
    for _, url in MACRO_FEEDS:
        pool.extend(_fetch(url, limit=per_feed))
    return _as_sources(_fresh(pool), n_max)


def collect(query, ticker=None, n_max=6):
    """analyst.gather 진입점. 티커 있으면 종목 RSS, 없으면 거시 RSS.
       반환: {ok, sources:[...], plan:[...], best_tier} (web.research main 과 동형)."""
    src = for_ticker(ticker, n_max) if ticker else []
    used = "Yahoo 종목 RSS"
    if not src:                       # 종목 기사 없으면(상장폐지·ETF 등) 거시로 보강
        src = macro(n_max)
        used = "거시 RSS(CNBC·MarketWatch·Investing)"
    for i, s in enumerate(src, 1):    # 전역 번호 재부여
        s["n"] = i
    return {
        "ok": bool(src),
        "count": len(src),
        "sources": src,
        "best_tier": min([s["tier"] for s in src], default=3),
        "plan": [used],
    }


if __name__ == "__main__":
    import sys, io, json
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    tk = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    r = collect(tk, ticker=tk)
    print(f"소스 {r['count']}개 (best_tier={r['best_tier']}, {r['plan']})")
    for s in r["sources"]:
        print(f"  [{s['n']}] ({s['tier_label']}·{s['domain']}·{s['date']}) {s['title'][:70]}")
