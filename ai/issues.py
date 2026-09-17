# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 이슈 알림 — AI·경제 소식을 '강도'로 걸러서 화면 배너 + 음성으로 알린다
#
#  흐름: 수집(키 0) → 묶기(같은 사건 여러 매체) → 강도 판정(파이썬 규칙) → 모드로 거르기
#        → 한 줄 한국어로 다듬기(로컬 LLM, GPU 한가할 때만) → 배너(toast.py) + TTS
#
#  강도 3단:  3=굵직  2=볼만함  1=잔잔
#  모드(분야별):  끔 / 굵직(3만) / 보통(2 이상) / 전부(1 이상)
#
#  ★강도는 LLM 이 안 정한다 — "숫자는 파이썬이"(COMET 이미 결론 난 것). 판정 근거:
#    · 경제: 지수·환율·코인 실측 등락폭(Yahoo) + 같은 사건 보도 매체 수 + 사건어(금리결정 등)
#    · AI  : 해커뉴스 점수(공개 API) + 같은 사건 보도 매체 수 + 사건어(출시·인수 등)
#    LLM 은 영어 제목을 한국어 한 줄로 옮기는 데만 쓴다. GPU 가 바쁘면 그것도 건너뛴다.
#
#  소스 — 전부 무료·키 없음:
#    뉴스  : 구글뉴스 RSS(news.fetch_keyword 재사용) / Yahoo chart(prices._yahoo 재사용)
#    AI    : 해커뉴스(hn.algolia.com) / 긱뉴스 1면 / 블루스카이 검색 / 허깅페이스 트렌딩 / GitHub 릴리즈 / 디시 특이점갤
#    경제  : 디시 주식·미주·코인갤 개념글 / 클리앙 새소식 / 블루스카이 검색
#    X(트위터)는 무료 읽기가 없어졌다(2026-02 종량제, 글당 $0.005) — X 에서 터진 건 위 커뮤니티로 옮겨온다.
#    레딧은 뺐다: 비인증 RSS 는 연달아 부르면 429, 공식 API 는 사전 승인제라 앱 생성이 거부됐다(2026-09-17).
#      영어권 반응은 블루스카이가 대신한다 — 로그인 없이 api.bsky.app 검색이 열려 있다(public.api.bsky.app 검색은 403).
#    에펨코리아는 뺐다: 최신순 목록엔 추천수가 거의 없고, 인기순은 레이아웃이 달라 추천수를 못 뽑는다(2026-09-15).
#
#  ★커뮤니티 단독은 '볼만함(2)'까지만. 같은 사건이 뉴스에도 잡혀야 '굵직'으로 올린다
#    — 한 군데서만 떠드는 건 대부분 소음이라서. (_merge_community)
# ═══════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import time
import subprocess
import urllib.request
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(_DIR, "issues_seen.json")
LOG_FILE = os.path.join(_DIR, "issues_log.json")
SUMMARY_MODEL = "gemma4:12b"
UA = "Mozilla/5.0 (COMET issues)"

LEVEL_NAME = {3: "굵직", 2: "볼만함", 1: "잔잔"}
MODE_MIN = {"끔": 99, "굵직": 3, "보통": 2, "전부": 1}
CAT_NAME = {"ai": "AI", "econ": "경제"}

NEWS_WINDOW_H = 12          # 이보다 오래된 기사는 안 봄
SEEN_KEEP_H = 48            # 알린 기록 보관 시간(같은 사건 재알림 방지)

# ── 사건어: 제목에 있으면 강도 +1 ────────────────────────────────
AI_EVENT = re.compile(
    r"(launch|release[sd]?|unveil|introduc|announce|acquir|lawsuit|sued|ban|outage|"
    r"open[- ]?source|GPT-?\d|Claude|Gemini \d|Llama \d|DeepSeek|"
    r"출시|공개|발표|인수|소송|규제|금지|장애|오픈소스)", re.I)
AI_TOPIC = re.compile(
    r"(\bAI\b|A\.I\.|LLM|GPT|OpenAI|Anthropic|Claude|Gemini|DeepMind|Llama|Mistral|"
    r"DeepSeek|Qwen|ollama|Nvidia|agent|인공지능|생성형|오픈AI|앤트로픽|엔비디아|챗GPT|제미나이)", re.I)
ECON_EVENT = re.compile(
    r"(기준금리|금리 (인상|인하|동결)|FOMC|파월|서킷브레이커|사이드카|폭락|급락|폭등|급등|"
    r"환율 (급등|급락|돌파)|금융위기|디폴트|부도|관세|전쟁|계엄|탄핵|"
    r"rate (hike|cut)|Fed (raises|cuts|holds)|crash|plunge|tariff|recession|default)", re.I)

ECON_TOPIC = re.compile(
    r"(주식|증시|코스피|코스닥|나스닥|S&P|금리|환율|달러|연준|FOMC|경제|물가|CPI|고용|관세|"
    r"비트코인|코인|부동산|채권|국채|유가|반도체주|실적|IPO|상장)", re.I)
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ── 경제 실측 등락 기준 (|등락%| ≥ 2단 / ≥ 3단) ──────────────────
MARKET_WATCH = [
    ("^KS11",   "코스피",   1.5, 2.5),
    ("^KQ11",   "코스닥",   2.0, 3.5),
    ("^GSPC",   "S&P500",   1.2, 2.2),
    ("^IXIC",   "나스닥",   1.5, 2.8),
    ("^SOX",    "필라델피아 반도체", 2.5, 4.5),
    ("KRW=X",   "원달러 환율", 0.6, 1.2),
    ("BTC-USD", "비트코인", 4.0, 7.0),
]


# ═══════════════════════════════════════════════════════════════
#  공통
# ═══════════════════════════════════════════════════════════════
def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _clean_title(t):
    return re.sub(r"\s+-\s+[^-]{1,40}$", "", t or "").strip()     # 구글뉴스 " - 매체명" 떼기


def _tokens(t):
    t = _clean_title(t).lower()
    return {w for w in re.findall(r"[0-9a-z가-힣]{2,}", t)
            if w not in {"the", "and", "for", "with", "from", "that", "this", "its", "are",
                         "has", "will", "new", "says", "after", "over", "into", "about",
                         "기자", "속보", "단독", "종합", "오늘", "뉴스"}}


def _similar(a, b):
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 3 or inter / max(1, min(len(a), len(b))) >= 0.6


def _cluster(items):
    """같은 사건끼리 묶는다 → [{title, sources:set, items:[...], tokens}]"""
    groups = []
    for it in items:
        tk = _tokens(it["title"])
        for g in groups:
            if _similar(tk, g["tokens"]):
                g["items"].append(it)
                g["sources"].add(it.get("source") or it.get("link", "")[:30])
                break
        else:
            groups.append({"title": _clean_title(it["title"]), "tokens": tk, "items": [it],
                           "sources": {it.get("source") or it.get("link", "")[:30]}})
    return groups


def _news(queries):
    import news
    out, cut = [], time.time() - NEWS_WINDOW_H * 3600
    for q, lang in queries:
        try:
            for it in news.fetch_keyword(q, lang=lang, limit=60):
                if (it.get("_ts") or 0) >= cut:
                    out.append(it)
        except Exception:
            pass
    return out


def _level_from_cluster(n_sources, event_hit, lv2, lv3):
    lv = 3 if n_sources >= lv3 else 2 if n_sources >= lv2 else 1
    if event_hit and n_sources >= 2:     # 매체 1곳짜리는 '급락' 한 단어로 안 올린다
        lv = min(3, lv + 1)
    return lv


# ═══════════════════════════════════════════════════════════════
#  커뮤니티 소스 — 추천수·점수가 곧 강도. 전부 한 페이지만, 실패하면 조용히 빈 목록
#  (각 함수가 돌려주는 건 {title, level, why, url, key, community:True})
# ═══════════════════════════════════════════════════════════════
def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def _strip_tags(t):
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", "", t or ""))).strip()


def _item(cat, title, level, why, url, lang="ko"):
    return {"cat": cat, "level": level, "title": title, "lang": lang, "why": why,
            "url": url, "key": url, "community": True, "tokens": sorted(_tokens(title))}


def src_geeknews(cat="ai", topic=AI_TOPIC):
    """긱뉴스 1면 — points. 30↑ 볼만함 / 10↑ 잔잔."""
    out, cut = [], time.time() - 36 * 3600
    s = _get("https://news.hada.io/")
    for row in s.split("<div class='topic_row'")[1:]:
        t = re.search(r"<h2 class='topic-title-heading'>(.*?)</h2>", row, re.S)
        pts = re.search(r"<span id='tp\d+'>(\d+)</span> points", row)
        ts = re.search(r'data-timestamp="(\d+)"', row)
        tid = re.search(r"topic\?id=(\d+)", row)
        if not (t and pts and tid):
            continue
        title, p = _strip_tags(t.group(1)), int(pts.group(1))
        if (ts and int(ts.group(1)) < cut) or not topic.search(title):
            continue
        lv = 2 if p >= 30 else 1 if p >= 10 else 0
        if lv:
            out.append(_item(cat, title, lv, f"긱뉴스 {p}점",
                             f"https://news.hada.io/topic?id={tid.group(1)}"))
    return out


# 블루스카이 검색은 OR 를 못 받는다(공백=AND, 'A OR B' 는 0건 — 2026-09-17 실측).
# 그래서 한 바퀴에 분야당 한 단어만 부르고, 바퀴마다 다음 단어로 돌린다.
BSKY_QUERIES = {
    "ai":   ["OpenAI", "Anthropic", "Gemini", "Nvidia", "DeepSeek", "LLM"],
    "econ": ["Federal Reserve", "tariffs", "stock market", "recession", "inflation"],
}
_bsky_turn = {"ai": 0, "econ": 0}
# ★비인증 검색은 몰아 부르면 IP 가 막힌다 — 몇 분 새 ~15회에 "administrative rules" 403, 10분 넘게 안 풀림(2026-09-17).
#   그래서 분야별 10분에 1번만 부르고, 그 사이 바퀴는 지난 결과를 다시 쓴다(가산점이 끊기지 않게).
#   403 이면 1시간 쉰다.
BSKY_GAP = 600
BSKY_BACKOFF = 3600
_bsky_cache = {}     # cat → (다음 호출 가능 시각, 지난 결과)


def src_bluesky(cat, query=None):
    """블루스카이 최근 24시간 인기글(키 없음). 좋아요 수로 반응 크기.
       검색어가 곧 분야 필터라 제목 단어 필터는 안 건다(영어 글이라 ECON_TOPIC 에 안 걸린다).
       query 를 주면(자가점검) 쉬는 시간을 무시하고 바로 부른다."""
    import urllib.error
    if query is not None:
        return _bsky_search(cat, query)
    ready, last = _bsky_cache.get(cat, (0, []))
    if time.time() < ready:
        return [dict(x) for x in last]
    qs = BSKY_QUERIES[cat]
    query = qs[_bsky_turn[cat] % len(qs)]
    _bsky_turn[cat] += 1
    try:
        got = _bsky_search(cat, query)
    except urllib.error.HTTPError as e:
        _bsky_cache[cat] = (time.time() + (BSKY_BACKOFF if e.code in (403, 429) else BSKY_GAP), [])
        raise
    _bsky_cache[cat] = (time.time() + BSKY_GAP, got)
    return [dict(x) for x in got]


def _bsky_search(cat, query):
    """검색 한 번 → 좋아요 300↑ 글만 잔잔(1)로."""
    from datetime import timedelta, timezone
    import urllib.parse
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    j = json.loads(_get("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?sort=top&lang=en"
                        f"&limit=25&since={since}&q=" + urllib.parse.quote(query)))
    out = []
    for p in j.get("posts", []):
        likes = int(p.get("likeCount") or 0)
        text = (p.get("record") or {}).get("text") or ""
        title = _strip_tags(text.split(chr(10))[0])[:100]       # 긴 본문은 단어가 많아 아무 뉴스와 엮인다
        # 레딧과 같은 선: 단독은 잔잔까지. 좋아요가 많아도 의견글·정치글이 많다.
        # 뉴스와 겹치면 _merge_community 가 뉴스 쪽을 올린다.
        if likes < 300 or len(title) < 15:
            continue
        rkey = p.get("uri", "").rsplit("/", 1)[-1]
        handle = (p.get("author") or {}).get("handle", "")
        out.append(_item(cat, title, 1, f"블루스카이 좋아요 {likes:,}",
                         f"https://bsky.app/profile/{handle}/post/{rkey}", lang="en"))
    return out


def src_hf_trending():
    """허깅페이스 트렌딩 모델 상위 10 중 최근 4일 안에 올라온 것. 좋아요 1500↑ 볼만함 / 400↑ 잔잔."""
    out, now = [], time.time()
    j = json.loads(_get("https://huggingface.co/api/trending?type=model&limit=10"))
    for rank, r in enumerate(j.get("recentlyTrending", []), 1):
        d = r.get("repoData") or {}
        mid, likes = d.get("id") or "", int(d.get("likes") or 0)
        try:
            age_d = (now - datetime.fromisoformat(d["lastModified"].replace("Z", "+00:00")).timestamp()) / 86400
        except Exception:
            continue
        if age_d > 4:
            continue
        lv = 2 if likes >= 1500 else 1 if likes >= 400 else 0
        if lv:
            it = _item("ai", f"허깅페이스 트렌딩 {rank}위: {mid}", lv, f"좋아요 {likes:,}",
                       f"https://huggingface.co/{mid}")
            it["ko"] = f"허깅페이스 트렌딩 {rank}위에 {mid.split('/')[-1]} 올라왔어"
            out.append(it)
    return out


GH_REPOS = ["ollama/ollama", "anthropics/claude-code"]


def src_github_releases():
    """쓰는 도구의 새 정식 릴리즈(48시간 안). x.y.0 = 볼만함, 패치 = 잔잔, rc/pre 는 무시."""
    out, cut = [], time.time() - 48 * 3600
    for repo in GH_REPOS:
        try:
            s = _get(f"https://github.com/{repo}/releases.atom")
        except Exception:
            continue
        for ent in s.split("<entry>")[1:4]:
            upd = re.search(r"<updated>([^<]+)</updated>", ent)
            title = re.search(r"<title>([^<]*)</title>", ent)
            link = re.search(r'<link[^>]+href="([^"]+)"', ent)
            if not (upd and title and link):
                continue
            tag = _strip_tags(title.group(1))
            if re.search(r"rc|pre|beta|alpha", tag, re.I):
                continue
            try:
                if datetime.fromisoformat(upd.group(1).replace("Z", "+00:00")).timestamp() < cut:
                    continue
            except Exception:
                continue
            m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", tag)
            lv = 2 if (m and m.group(3) == "0") else 1
            name = repo.split("/")[-1]
            it = _item("ai", f"{name} {tag} 릴리즈", lv, "GitHub 릴리즈", link.group(1))
            it["ko"] = f"{name} 새 버전 {tag} 나왔어"
            out.append(it)
    return out


def src_dcinside(gid, kind, cat, lv2, lv1, label, topic=None):
    """디시 개념글(추천 모드) — 추천수로 강도. kind='board'(정식갤) | 'mgallery/board'(마이너)."""
    out, cut = [], time.time() - 24 * 3600
    s = _get(f"https://gall.dcinside.com/{kind}/lists/?id={gid}&exception_mode=recommend")
    for row in s.split('<tr class="ub-content')[1:]:
        a = re.search(r'<td class="gall_tit[^"]*">.*?<a\s+href="([^"]+)"[^>]*>(.*?)</a>', row, re.S)
        rec = re.search(r'<td class="gall_recommend">(\d+)<', row)
        dt = re.search(r'<td class="gall_date" title="([^"]+)"', row)
        if not (a and rec and dt):
            continue
        try:
            if datetime.strptime(dt.group(1), "%Y-%m-%d %H:%M:%S").timestamp() < cut:
                continue
        except Exception:
            continue
        title, r = _strip_tags(a.group(2)), int(rec.group(1))
        if topic and not topic.search(title):
            continue
        lv = 2 if r >= lv2 else 1 if r >= lv1 else 0
        if lv:
            out.append(_item(cat, title, lv, f"{label} 추천 {r}",
                             "https://gall.dcinside.com" + a.group(1).split("&exception_mode")[0]))
    return out


def src_clien(cat="econ", topic=ECON_TOPIC):
    """클리앙 새소식 — 조회수. 6000↑ 볼만함 / 3000↑ 잔잔 (36시간 안).
       ★공감수가 아니라 조회수: 이 게시판은 공감이 거의 안 붙는다(30건 중 최대 2, 2026-09-15 실측).
       IT·AI 소식이 많고 경제는 적다 — 분야 단어로 거른다."""
    out, cut = [], time.time() - 36 * 3600
    s = _get("https://www.clien.net/service/board/news")
    for row in s.split('data-role="list-row"')[1:]:
        hit = re.search(r'<span class="hit">\s*([\d,]+)\s*</span>', row)
        t = re.search(r'data-role="list-title-text" title="([^"]*)"', row)
        href = re.search(r'class="list_subject" href="([^"?]+)', row)
        ts = re.search(r'<span class="timestamp">([^<]+)</span>', row)
        if not (hit and t and href):
            continue
        try:
            if ts and datetime.strptime(ts.group(1).strip(), "%Y-%m-%d %H:%M:%S").timestamp() < cut:
                continue
        except Exception:
            pass
        title, n = _strip_tags(t.group(1)), int(hit.group(1).replace(",", ""))
        if topic and not topic.search(title):
            continue
        lv = 2 if n >= 6000 else 1 if n >= 3000 else 0
        if lv:
            out.append(_item(cat, title, lv, f"클리앙 조회 {n:,}", "https://www.clien.net" + href.group(1)))
    return out


_FAILED = []    # 이번 회차에 조회 실패한 소스 — '반응 없음'이 아니라 '확인 못 함'


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        _FAILED.append(f"{fn.__name__}: {str(e)[:40]}")
        return []


def _merge_community(found):
    """커뮤니티 단독은 볼만함까지. 같은 사건이 뉴스·실측에도 있으면 그쪽 강도 +1 하고 커뮤니티 건은 합친다."""
    press = [x for x in found if not x.get("community")]
    out = list(press)
    for c in (x for x in found if x.get("community")):
        tk = set(c["tokens"])
        twin = next((p for p in press if _similar(tk, set(p.get("tokens") or []))), None)
        if twin:
            if "커뮤니티" not in twin["why"]:
                twin["level"] = min(3, twin["level"] + 1)
                twin["why"] += f" + 커뮤니티 반응({c['why']})"
            continue
        c["level"] = min(2, c["level"])
        out.append(c)
    return out


# ═══════════════════════════════════════════════════════════════
#  수집 — 분야별
# ═══════════════════════════════════════════════════════════════
def collect_ai():
    found = []
    # 1) 해커뉴스 1면 — 점수가 곧 강도
    try:
        url = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        hits = json.load(urllib.request.urlopen(req, timeout=12)).get("hits", [])
        for h in hits:
            t = h.get("title") or ""
            if not AI_TOPIC.search(t):
                continue
            pts = int(h.get("points") or 0)
            lv = 3 if pts >= 600 else 2 if pts >= 250 else 1
            found.append({"cat": "ai", "level": lv, "title": t, "lang": "en",
                          "why": f"해커뉴스 {pts}점",
                          "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                          "tokens": sorted(_tokens(t))})
    except Exception:
        pass
    # 2) 구글뉴스 — 같은 사건을 여러 매체가 쓰면 강도↑
    items = _news([("OpenAI OR Anthropic OR Gemini OR DeepSeek OR Nvidia AI when:1d", "en"),
                   ("인공지능 OR 생성형AI OR 오픈AI OR 앤트로픽 OR 엔비디아 when:1d", "ko")])
    for g in _cluster(items):
        if not AI_TOPIC.search(g["title"]):
            continue
        n = len(g["sources"])
        lv = _level_from_cluster(n, bool(AI_EVENT.search(g["title"])), lv2=3, lv3=7)
        found.append({"cat": "ai", "level": lv, "title": g["title"],
                      "lang": g["items"][0].get("lang", "ko"), "why": f"{n}개 매체 보도",
                      "url": g["items"][0].get("link", ""), "tokens": sorted(g["tokens"])})
    found += _safe(src_geeknews)
    found += _safe(src_bluesky, "ai")
    found += _safe(src_hf_trending)
    found += _safe(src_github_releases)
    found += _safe(src_dcinside, "thesingularity", "mgallery/board", "ai", 150, 60, "디시 특이점갤", AI_TOPIC)
    found += _safe(src_clien, "ai", AI_TOPIC)
    return _merge_community(found)


def collect_econ():
    found = []
    # 1) 실측 등락 — 가장 믿을 만한 강도
    try:
        import prices
        for sym, name, t2, t3 in MARKET_WATCH:
            r = prices._yahoo(sym, "시세")
            pct = r.get("change_pct") if r.get("ok") else None
            if pct is None:
                continue
            a = abs(pct)
            lv = 3 if a >= t3 else 2 if a >= t2 else 0
            if not lv:
                continue
            verb = "급등" if pct > 0 else "급락"
            if sym == "KRW=X":
                verb = "원화 약세" if pct > 0 else "원화 강세"
            found.append({"cat": "econ", "level": lv, "lang": "ko",
                          "title": f"{name} {pct:+.2f}% {verb}",
                          "ko": f"{name}가 {pct:+.1f}% 움직였어 ({verb})",
                          "why": "Yahoo 실측", "url": "",
                          # 같은 날·같은 종목은 한 사건 — 단계가 오를 때만 다시 알린다
                          "key": f"mkt:{sym}:{datetime.now():%Y-%m-%d}",
                          "tokens": [sym.lower(), name]})
    except Exception:
        pass
    # 2) 뉴스 — 사건어 + 매체 수
    items = _news([("경제 OR 금리 OR 환율 OR 증시 when:1d", "ko"),
                   ("Fed OR stock market OR tariff OR recession when:1d", "en")])
    for g in _cluster(items):
        n = len(g["sources"])
        ev = bool(ECON_EVENT.search(g["title"]))
        lv = _level_from_cluster(n, ev, lv2=4, lv3=8)
        if not ev and lv < 2:
            continue                       # 사건어도 없고 보도도 적으면 소음
        found.append({"cat": "econ", "level": lv, "title": g["title"],
                      "lang": g["items"][0].get("lang", "ko"), "why": f"{n}개 매체 보도",
                      "url": g["items"][0].get("link", ""), "tokens": sorted(g["tokens"])})
    # 디시는 분야 단어 필터 필수 — 없으면 '대통령 티어표.jpg' 같은 밈이 추천 100+ 로 올라온다(2026-09-15)
    found += _safe(src_dcinside, "neostock", "board", "econ", 150, 60, "디시 주식갤", ECON_TOPIC)
    found += _safe(src_dcinside, "stockus", "mgallery/board", "econ", 150, 60, "디시 미주갤", ECON_TOPIC)
    found += _safe(src_dcinside, "bitcoins_new1", "board", "econ", 150, 60, "디시 코인갤", ECON_TOPIC)
    found += _safe(src_clien)
    found += _safe(src_bluesky, "econ")
    return _merge_community(found)


COLLECTORS = {"ai": collect_ai, "econ": collect_econ}


# ═══════════════════════════════════════════════════════════════
#  중복 제거 — 이미 알린 사건은 강도가 올라갔을 때만 다시
# ═══════════════════════════════════════════════════════════════
def _filter_new(found, seen):
    now = time.time()
    seen[:] = [s for s in seen if now - s.get("t", 0) < SEEN_KEEP_H * 3600]
    fresh = []
    for it in sorted(found, key=lambda x: -x["level"]):
        tk = set(it.get("tokens") or [])
        # ★처음 걸린 하나만 보면 안 된다 — 옛날에 낮게 본 기록이 먼저 걸리면 '강도가 올랐다'로 착각해
        #   이미 굵직으로 알린 걸 매 바퀴 다시 알렸다(2026-09-17, Emerald AI 3연속). 걸린 것 중 최고 강도와 비교.
        prev = 0
        for s in seen + fresh:
            if s.get("cat") != it["cat"]:
                continue
            if (it.get("key") and s.get("key") == it.get("key")) or \
               (not it.get("key") and not s.get("key") and _similar(tk, set(s.get("tokens") or []))):
                prev = max(prev, s.get("level", 0))
        if prev >= it["level"]:
            continue
        fresh.append(it)
    return fresh


def _mark_seen(items, seen):
    now = time.time()
    for it in items:
        seen.append({"cat": it["cat"], "level": it["level"], "key": it.get("key"),
                     "tokens": it.get("tokens"), "title": it["title"], "t": now})


# ═══════════════════════════════════════════════════════════════
#  한 줄 한국어로 — 로컬 LLM(사고 끔). GPU 바쁘면 건너뛰고 원제목 사용
# ═══════════════════════════════════════════════════════════════
def _koreanize(items):
    todo = [it for it in items if not it.get("ko")]
    if not todo:
        return
    try:
        import ollama
        listing = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(todo))
        prompt = ("아래 뉴스 제목들을 각각 한국어 한 문장(35자 이내)으로 옮겨라. 과장·추측 금지, "
                  "제목에 없는 사실 추가 금지, 반말로 끝내라(예: '~했대', '~나왔어').\n"
                  'JSON 만: {"items":[{"i":0,"ko":"..."}]}\n\n' + listing)
        r = ollama.Client(timeout=90).chat(
            model=SUMMARY_MODEL, format="json", think=False, keep_alive="2m",
            options={"temperature": 0, "num_predict": 800},
            messages=[{"role": "user", "content": prompt}])
        for row in json.loads(r["message"]["content"]).get("items", []):
            i = int(row.get("i", -1))
            if 0 <= i < len(todo) and (row.get("ko") or "").strip():
                todo[i]["ko"] = row["ko"].strip()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  전달 — 화면 배너 + 음성
# ═══════════════════════════════════════════════════════════════
def popup(tag, title, body="", level=2, sec=8.0):
    """toast.py 를 따로 띄운다(안 기다림)."""
    py = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(py):
        py = sys.executable
    subprocess.Popen([py, os.path.join(_DIR, "toast.py"), "--tag", tag, "--title", title,
                      "--body", body, "--level", str(level), "--sec", str(sec)],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _say(text):
    try:
        import voice
        voice.Mouth().speak(text)
    except Exception as e:
        print(f"  [이슈 TTS 오류] {e}")


def deliver(cat, items, *, speak=True, show=True, log=print):
    if not items:
        return
    items = sorted(items, key=lambda x: -x["level"])
    top = items[0]
    name = CAT_NAME.get(cat, cat)
    line = top.get("ko") or top["title"]
    more = f" 외 {len(items) - 1}건" if len(items) > 1 else ""
    tag = f"COMET · {name} · {LEVEL_NAME[top['level']]}"
    body = top.get("why", "") + more
    if len(items) > 1:
        body += "\n" + "\n".join("· " + (x.get("ko") or x["title"])[:48] for x in items[1:3])
    if show:
        popup(tag, line, body, top["level"], sec=6 + 2 * top["level"])
    if speak:
        head = "굵직한 " if top["level"] == 3 else ""
        _say(f"{name} 쪽에 {head}소식 있어. {line}.{(' 그 외 ' + str(len(items) - 1) + '건 더 있어.') if more else ''}")
    logs = _load(LOG_FILE, [])
    for it in items:
        logs.append({"at": datetime.now().isoformat(timespec="seconds"), "cat": cat,
                     "level": it["level"], "title": it["title"], "ko": it.get("ko"),
                     "why": it.get("why"), "url": it.get("url")})
    _save(LOG_FILE, logs[-300:])
    log(f"[이슈] {name} {len(items)}건 알림 — {line}")


# ═══════════════════════════════════════════════════════════════
#  한 번 점검 — 자율모드 스레드가 주기적으로 부른다
# ═══════════════════════════════════════════════════════════════
def check(modes, *, gpu_busy_fn=None, quiet_hours=None, log=print, dry=False, baseline=False):
    """modes = {"ai":"굵직", "econ":"보통"} → 알린 건수 dict.
       baseline=True: 지금 떠 있는 건 '본 것'으로만 등록(처음 켤 때 몰아서 쏟아지는 것 방지)."""
    seen = _load(SEEN_FILE, [])
    busy, _ = gpu_busy_fn() if gpu_busy_fn else (False, "")
    hour = datetime.now().hour
    quiet = bool(quiet_hours) and (
        quiet_hours[0] <= hour < quiet_hours[1] if quiet_hours[0] < quiet_hours[1]
        else hour >= quiet_hours[0] or hour < quiet_hours[1])
    result = {}
    for cat, mode in modes.items():
        need = MODE_MIN.get(mode, 99)
        if need > 3 or cat not in COLLECTORS:
            continue
        del _FAILED[:]
        try:
            found = COLLECTORS[cat]()
        except Exception as e:
            log(f"[이슈] {cat} 수집 실패: {e}")
            continue
        if _FAILED:
            log(f"[이슈] {cat} 이번엔 확인 못 한 소스: {' / '.join(_FAILED)}")
        new = _filter_new(found, seen)
        if baseline:
            _mark_seen(new, seen)
            result[cat] = 0
            continue
        hit = [x for x in new if x["level"] >= need][:5]
        _mark_seen(new, seen)              # 기준 미달도 '봤음' — 나중에 강도 오르면 그때 알림
        if not hit:
            result[cat] = 0
            continue
        if not busy:
            _koreanize(hit)
        if dry:
            for x in hit:
                log(f"  [{LEVEL_NAME[x['level']]}] {x.get('ko') or x['title']}  ({x['why']})")
        else:
            # 게임 중(GPU 바쁨)엔 배너가 전체화면을 깰 수 있어 소리만. 조용한 시간엔 배너만.
            deliver(cat, hit, speak=not quiet, show=not busy, log=log)
        result[cat] = len(hit)
    _save(SEEN_FILE, seen)
    return result


SOURCES = {   # 소스별 생존 확인(자가점검용) — 0건이 며칠 이어지면 사이트 구조가 바뀐 것
    "긱뉴스": lambda: src_geeknews(topic=re.compile(".")),
    "블루스카이(AI)": lambda: src_bluesky("ai", "OpenAI"),
    "블루스카이(경제)": lambda: src_bluesky("econ", "Federal Reserve"),
    "허깅페이스": lambda: json.loads(_get("https://huggingface.co/api/trending?type=model&limit=10"))["recentlyTrending"],
    "GitHub": lambda: [r for r in GH_REPOS if "<entry>" in _get(f"https://github.com/{r}/releases.atom")],
    "디시 특이점갤": lambda: src_dcinside("thesingularity", "mgallery/board", "ai", 1, 0, "특갤"),
    "디시 주식갤": lambda: src_dcinside("neostock", "board", "econ", 1, 0, "주갤"),
    "클리앙": lambda: src_clien(topic=None),
}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if "--sources" in sys.argv:
        for name, fn in SOURCES.items():
            t = time.time()
            try:
                n = len(fn())
                print(f"  {'OK ' if n else '0건'} {n:>3}  {time.time() - t:4.1f}s  {name}")
            except Exception as e:
                print(f"  실패      {name}: {str(e)[:70]}")
        sys.exit()
    for cat, fn in COLLECTORS.items():
        t = time.time()
        rows = sorted(fn(), key=lambda x: -x["level"])
        print(f"\n== {CAT_NAME[cat]}: {len(rows)}건 ({time.time() - t:.1f}s) ==")
        for x in rows[:12]:
            print(f"  [{LEVEL_NAME[x['level']]}] {x['title'][:80]}  ({x['why']})")
