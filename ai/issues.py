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
#    뉴스  : 구글뉴스 RSS(news.fetch_keyword 재사용) + 매체 RSS 직접 구독(NEWS_FEEDS)
#            / Yahoo chart(prices._yahoo 재사용)
#    AI    : 해커뉴스(hn.algolia.com) / 긱뉴스 1면 / Techmeme / 블루스카이 / 마스토돈 인기글 /
#            Lobsters 1면 / GitHub 트렌딩·릴리즈 / 허깅페이스 트렌딩·데일리페이퍼 / arXiv cs.AI / 디시 특이점갤
#    경제  : 연합뉴스·매경·Investing·MarketWatch RSS / 디시 주식·미주·코인갤 개념글 /
#            클리앙 새소식 / 블루스카이 / 마스토돈 인기글
#    X(트위터)는 무료 읽기가 없어졌다(2026-02 종량제, 글당 $0.005) — X 에서 터진 건 위 커뮤니티로 옮겨온다.
#    레딧은 뺐다: 비인증 RSS 는 연달아 부르면 429, 공식 API 는 사전 승인제라 앱 생성이 거부됐다(2026-09-17).
#      영어권 반응은 블루스카이+마스토돈이 대신한다(블루스카이가 403 으로 쉬는 동안 마스토돈이 남는다).
#    에펨코리아는 뺐다: 최신순 목록엔 추천수가 거의 없고, 인기순은 레이아웃이 달라 추천수를 못 뽑는다(2026-09-15).
#
#  ★사실 보도가 아닌 소스는 `frame` 을 달아 그렇게 읽히게 한다(개인 의견 / 논문) — _frame_fallback.
#  ★새 피드를 넣을 땐 200 OK 만 보지 말고 최신 pubDate 를 볼 것. 2년 전에 멈춘 피드가 실재한다
#    (Investing news_285, 2024-11-26 정지).
#
#  ★커뮤니티 단독은 '볼만함(2)'까지만. 같은 사건이 뉴스에도 잡혀야 '굵직'으로 올린다
#    — 한 군데서만 떠드는 건 대부분 소음이라서. (_merge_community)
# ═══════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import time
import threading
import subprocess
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache

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
    """임시 파일에 다 쓴 뒤 바꿔 끼운다 — 쓰는 도중 데몬이 꺼지면 반쯤 쓴 파일이 남아 '본 것'이 통째로 비었다
       (본 것이 비면 다음 판에 지금 떠 있는 걸 전부 새 소식으로 본다)."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def _ga(word):
    """이/가 — 받침 있으면 '이'. 숫자·영문으로 끝나면 읽는 소리로(0영 1일 3삼 6육 7칠 8팔, L엘 M엠 N엔 R알)."""
    c = (word or " ")[-1]
    if "가" <= c <= "힣":
        return "이" if (ord(c) - 0xAC00) % 28 else "가"
    return "이" if c in "013678LMNRlmnr" else "가"


def _clean_title(t):
    return re.sub(r"\s+-\s+[^-]{1,40}$", "", t or "").strip()     # 구글뉴스 " - 매체명" 떼기


def _tokens(t):
    t = _clean_title(t).lower()
    return {w for w in re.findall(r"[0-9a-z가-힣]{2,}", t)
            if w not in {"the", "and", "for", "with", "from", "that", "this", "its", "are",
                         "has", "will", "new", "says", "after", "over", "into", "about",
                         "기자", "속보", "단독", "종합", "오늘", "뉴스"}}


def _similar(a, b):
    """긴 글 둘이 사실상 같은 말인가(detail_of_last 의 요약·원문 비교용). 사건 판정은 _same_event."""
    if not a or not b:
        return False
    inter = len(a & b)
    return inter >= 3 or inter / max(1, min(len(a), len(b))) >= 0.6


# ═══════════════════════════════════════════════════════════════
#  같은 사건인가 — 제목 둘을 비교한다. 지금은 '이미 알린 것 거르기'(_filter_new)에만 쓴다.
#   ★묶기(_cluster)·커뮤니티 합치기는 옛 판정 그대로 뒀다 — 기사 원재료 243건(10/07)으로 돌려 보니
#     새 판정으로 바꾸면 '몇 개 매체 보도' 가 크게 줄어 굵직·볼만함이 거의 안 나온다(강도 기준이 바뀌는 일).
#     옛 묶기는 반대로 다른 기사를 많이 붙인다('Nvidia 보안 플랫폼' 에 보험사·위키미디어·규제 칼럼까지) —
#     매체 수가 부풀고 붙은 기사는 묻힌다. 묶기 전용 판정을 따로 맞춰야 하는 일이라 남겨 둔다.
#   ★2026-10-07 호윤: "중복으로 가져오는 것도 많더라". 알림 기록 265건을 손으로 묶어 보니 예전 판정(_similar)은
#     · 조사·표기가 다르면 다른 사건 — '정책실장에'·'정책실장', '오퍼스'·'오푸스', '앤트로픽'·'앤스로픽'
#     · 해커뉴스의 짧은 제목('Claude Opus 5.5')과 기사 제목을 못 이었다
#     · 반대로 'to·in·ai' 같은 흔한 세 단어만 겹쳐도 같은 사건이라 다른 사건을 묻었다
#       ('Anthropic's IPO Will Be AI's Next Moment of Crisis' = 'Anthropic says Claude … its next AI')
#   그래서 ① 조사·어미 떼기 ② 표기 흔들림을 한 이름으로 ③ 흔한 말은 가볍게(0.3) ④ 제품+버전은 무겁게(gemini4 = 2)
#   ⑤ 겹친 무게가 2 이상이면서 짧은 쪽 무게의 60% 이상일 때만 같은 사건.
#   정답지 쌍 비교(48시간 안·같은 분야): 같은 사건 맞힘 52→63 · 다른 사건을 같다고 함 8→0.
#   ★문턱을 낮추면(0.5) 'GPT-6 Sol·Luna' 와 'GPT-6 Astra 에니그마' 처럼 같은 제품군 다른 사건이 붙는다 — 0.6 유지.
#   영어↔한국어로 말을 바꾼 기사(해킹·R&D 26% 등)는 여전히 못 잇는다 — 낱말 비교의 한계.
# ═══════════════════════════════════════════════════════════════
_CJK = {"美": " 미국 ", "中": " 중국 ", "日": " 일본 ", "韓": " 한국 ", "靑": " 청와대 ", "佛": " 프랑스 ",
        "獨": " 독일 ", "英": " 영국 ", "北": " 북한 ", "與": " 여당 ", "野": " 야당 "}
_EV_ALIAS = {     # 회사·제품·사람·자주 나오는 사건어 — 영어/한글/표기 흔들림을 하나로
    "오픈에이아이": "openai", "앤트로픽": "anthropic", "앤스로픽": "anthropic", "클로드": "claude",
    "오퍼스": "opus", "오푸스": "opus", "소넷": "sonnet", "하이쿠": "haiku", "제미나이": "gemini",
    "제미니": "gemini", "구글": "google", "엔비디아": "nvidia", "nvda": "nvidia", "마이크로소프트": "microsoft",
    "ms": "microsoft", "메타": "meta", "애플": "apple", "아마존": "amazon", "테슬라": "tesla",
    "딥시크": "deepseek", "큐웬": "qwen", "미스트랄": "mistral", "화웨이": "huawei", "알리바바": "alibaba",
    "삼성": "samsung", "삼성전자": "samsung", "챗지피티": "chatgpt", "올트먼": "altman", "알트먼": "altman",
    "아모데이": "amodei", "젠슨": "jensen", "버핏": "buffett", "머스크": "musk", "트럼프": "trump",
    "파월": "powell", "아스트라": "astra", "루나": "luna", "나스닥": "nasdaq", "코스피": "kospi",
    "코스닥": "kosdaq", "연준": "fed", "fomc": "fed", "일본은행": "boj", "한은": "bok", "한국은행": "bok",
    "상장": "ipo", "엔스케일": "nscale", "해커": "hack", "해커들": "hack", "해킹": "hack", "hacker": "hack",
    "hacked": "hack", "hacking": "hack", "침투": "hack", "breach": "hack", "breached": "hack",
    "lg전자": "lg", "lg엔솔": "lg", "lg에너지솔루션": "lg",
    "연기": "delay", "미뤄": "delay", "미룰": "delay", "미룬": "delay", "shift": "delay", "postpone": "delay",
    "delayed": "delay", "january": "1월", "february": "2월", "march": "3월", "april": "4월",   # may 는 조동사라 뺐다
    "june": "6월", "july": "7월", "august": "8월", "september": "9월", "october": "10월",
    "november": "11월", "december": "12월",
}
#  흔한 말 — 겹쳐도 같은 사건이라는 근거가 약하다. 실제 제목 389개(알림 기록+본 것)의 문서 빈도 위쪽에서 골랐다.
_EV_LOW = {"ai", "openai", "anthropic", "nvidia", "google", "microsoft", "meta", "apple", "amazon",
           "agent", "model", "모델", "stock", "market", "주식", "증시", "시장", "금리", "인상", "급등", "급락",
           "상승", "하락", "환율", "경제", "data", "safety", "안전", "공개", "출시", "발표", "release", "launch",
           "unveil", "announce", "new", "새", "신규", "미국", "us", "한국", "중국", "일본", "인공지능", "생성형",
           "llm", "gpu", "ceo", "트렌딩", "github", "깃허브", "허깅페이스", "논문", "paper", "trump",
           "investor", "기업", "chatgpt", "tech", "2025", "2026", "2027"}
_EV_PRODUCT = {"claude", "opus", "sonnet", "haiku", "gemini", "gpt", "llama", "qwen", "mistral", "large",
               "grok", "deepseek", "phi", "gemma"}
_EV_STOP = {"the", "and", "for", "with", "from", "that", "this", "its", "are", "has", "will", "says", "after",
            "over", "into", "about", "to", "in", "of", "on", "is", "it", "be", "as", "an", "if", "at", "by",
            "up", "can", "could", "should", "what", "more", "how", "why", "a", "or", "not", "but", "you",
            "your", "we", "our", "they", "their", "he", "she", "his", "her", "was", "were", "been", "have",
            "had", "do", "does", "did", "than", "then", "so", "just", "now", "here", "there", "who", "which",
            "while", "amid", "via", "per", "vs", "s", "t", "exclusive", "breaking", "report", "reports",
            "기자", "속보", "단독", "종합", "오늘", "뉴스", "1보", "2보"}
#  조사·어미 — 긴 것부터. 남는 말이 두 글자 밑이면 안 뗀다('평가'·'속도'·'물가' 는 그대로)
_KO_TAIL = ("으로부터", "에서는", "에게서", "했다고", "한다고", "됐다고", "이라고", "라고",
            "했어요", "했대요", "했어", "했대", "한대", "했다", "한다", "된다", "됐다", "됐어", "됐대",
            "하며", "하고", "해서", "하나", "할까", "인가", "이다", "였다", "에서", "으로", "에게", "까지", "부터",
            "에선", "에도", "이며", "보다", "처럼", "마저", "조차",
            "에", "은", "는", "이", "가", "을", "를", "의", "와", "과", "도", "로", "만", "엔", "께", "랑")


def _ko_stem(w):
    for _ in range(3):                     # '정책실장에도' → '정책실장에' → '정책실장'
        for tail in _KO_TAIL:
            if w.endswith(tail) and len(w) - len(tail) >= 2:
                w = w[: -len(tail)]
                break
        else:
            break
    return w


@lru_cache(maxsize=8192)
def _ev_tokens(title):
    """제목 → {낱말: 무게}. 같은 제목은 다시 안 쪼갠다(본 것 수백 개와 매 바퀴 비교하므로)."""
    t = re.sub(r"\s+-\s+[^-]{1,40}$", "", title or "").strip()           # " - 매체명"
    t = re.sub(r"\s+(?:By|by)\s+[^,|]{1,30}$", "", t)                    # " By 인포스탁데일리"
    t = re.sub(r"\s+\|\s+[^|]{1,40}$", "", t)                            # " | AI (artificial intelligence)"
    t = re.sub(r"^\s*(?:exclusive|breaking|속보|단독)\s*[|:]\s*", "", t, flags=re.I)
    for _ in range(2):
        t = re.sub(r"^\s*[\[【(][^\]】)]{1,20}[\]】)]\s*", "", t)           # [속보] [AI는 지금] (종합)
    for k, v in _CJK.items():
        t = t.replace(k, v)
    t = t.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    t = t.replace("오픈ai", " openai ").replace("챗gpt", " chatgpt ")        # 한글+영문이 붙은 이름
    t = re.sub(r"\bs\s*&\s*p\b(?:\s*\d+)?", " sp500 ", t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)                               # 3,800 → 3800
    raw = re.findall(r"[a-z]+[0-9]*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?(?:[월년배위건명]|분기)?|[가-힣]+", t)
    seq = []
    for w in raw:
        if re.match(r"[가-힣]", w):
            w = _ko_stem(w)
        elif re.match(r"[a-z]", w) and len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]                                                    # stocks → stock
        seq.append(_EV_ALIAS.get(w, w))
    out = {}
    for i, w in enumerate(seq):
        if w in _EV_STOP:
            continue
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", w):
            if i > 0 and seq[i - 1] in _EV_PRODUCT:                        # gemini 4 → gemini4
                out[seq[i - 1] + w] = 2.0
            if "." in w or len(w) >= 2:
                out[w] = 0.3 if w in _EV_LOW else 0.7
            continue
        if re.match(r"[0-9]", w):                                          # 11월 · 31년 · 3위
            out[w] = 0.7
            continue
        m = re.fullmatch(r"([a-z]+)([0-9]+(?:\.[0-9]+)?)", w)             # gpt6 · qwen3.5
        if m and m.group(1) in _EV_PRODUCT:
            out[w] = 2.0
            out[m.group(1)] = 0.6
            continue
        if len(w) >= 2:
            out[w] = 0.3 if w in _EV_LOW else 0.6 if w in _EV_PRODUCT else 1.0
    return out


def _same_event(a, b):
    ta, tb = _ev_tokens(a or ""), _ev_tokens(b or "")
    if not ta or not tb:
        return False
    shared = sum(ta[w] for w in ta.keys() & tb.keys())
    return shared >= 2.0 and shared >= 0.6 * min(sum(ta.values()), sum(tb.values()))


def _cluster(items):
    """같은 사건끼리 묶는다 → [{title, sources:set, items:[...], tokens}]"""
    groups = []
    for it in items:
        tk = _tokens(it["title"])
        for g in groups:
            if _similar(tk, g["tokens"]):    # ★여긴 옛 판정 그대로 — 위 _same_event 머리말의 '묶기' 참고
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


# ── 매체 피드 직접 구독 ──────────────────────────────────────────
#  구글뉴스는 '키워드에 걸린 것'만 준다 — 키워드를 안 스친 사건은 통째로 안 보인다.
#  그래서 매체 피드를 그대로 받아 같은 _cluster 에 섞는다. 매체가 늘면 '몇 개 매체 보도'가
#  제대로 세지므로, 건수만 느는 게 아니라 강도 판정도 같이 정확해진다.
#  ★한경(hankyung.com/feed/economy)은 403 이라 뺐다(2026-09-18 실측).
#  넷째 칸 = '물어볼 때만'. True 면 자동 2분 점검에서 안 부르고 호윤이 '뉴스' 라고 칠 때만 부른다.
#  ★연합뉴스 경제 RSS 가 그렇다 — 77건 중 사건어가 걸린 건 1건뿐이고 그마저 구글뉴스에 이미 있었다.
#    나머지는 '[게시판] 기금 출연' 같은 보도자료라 자동 알림엔 보탬이 안 된다(호윤 판단, 2026-09-18).
NEWS_FEEDS = {
    "ai": [("Techmeme", "https://www.techmeme.com/feed.xml", "en", False)],
    # ★Investing 은 news_25 를 쓴다 — news_285 는 2024-11-26 에 멈춘 죽은 피드다(2026-09-18 실측).
    #   피드를 새로 넣을 땐 건수만 보지 말고 최신 pubDate 를 꼭 볼 것. 200 OK 로도 2년 전 걸 준다.
    "econ": [("연합뉴스", "https://www.yna.co.kr/rss/economy.xml", "ko", True),
             ("매일경제", "https://www.mk.co.kr/rss/30100041/", "ko", False),
             ("Investing", "https://www.investing.com/rss/news_25.rss", "en", False),
             ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "en", False)],
}


def _feed_ts(s):
    """RFC822(대부분) + 'YYYY-MM-DD HH:MM:SS'(Investing) 둘 다. 못 읽으면 0.
       ★0 이면 12시간 창에서 통째로 버려진다 — Investing 이 이걸로 전부 날아갔었다."""
    from email.utils import parsedate_to_datetime
    s = (s or "").strip()
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", s)
    if m:
        from datetime import timezone
        return datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp()
    return 0.0


def _rss(name, url, lang):
    """매체 RSS 한 개 → news.fetch_keyword 와 같은 모양(_cluster 가 그대로 먹을 수 있게)."""
    import xml.etree.ElementTree as ET
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=12).read())
    out = []
    for it in root.findall(".//item")[:60]:
        ts = _feed_ts(it.findtext("pubDate", ""))
        title = _strip_tags(it.findtext("title", ""))
        if not title:
            continue
        out.append({"title": title, "link": (it.findtext("link", "") or "").strip(),
                    "source": name, "lang": lang, "_ts": ts,
                    "summary": _strip_tags(it.findtext("description", ""))[:300]})
    return out


def _feeds(cat, asked=False):
    """그 분야 매체 피드. 하나가 죽어도 나머지는 온다 — 죽은 건 '확인 못 한 소스'로 남는다.
       asked=True(호윤이 직접 물어본 것)면 '물어볼 때만' 짝지어둔 피드까지 같이 받는다."""
    out, cut = [], time.time() - NEWS_WINDOW_H * 3600
    for name, url, lang, ask_only in NEWS_FEEDS.get(cat, []):
        if ask_only and not asked:
            continue
        try:
            out += [it for it in _rss(name, url, lang) if (it.get("_ts") or 0) >= cut]
        except Exception as e:
            _FAILED.append(f"{name}: {str(e)[:100]}")
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
# ★2026-09-18 에 또 403. 쉬는 시간이 메모리에만 있어서 데몬을 재시작할 때마다 0 으로 돌아갔고,
#   막힌 줄 모르고 바로 다시 때렸다(12:40 로그 = 재시작 2분 뒤 또 403). 그래서 쉬는 시각을 파일에 남긴다.
#   그리고 ai·econ 이 15초 간격으로 연달아 때리던 것도 붙여놨다 — 호출 사이 최소 BSKY_MIN_GAP 을 둔다.
BSKY_GAP = 600
BSKY_BACKOFF = 3600
BSKY_MIN_GAP = 90        # 분야가 달라도 이 간격 안엔 두 번 안 부른다
BSKY_STALE_H = 3         # 막혀 있는 동안 지난 결과를 재활용하는 한계(24시간 인기글이라 이 정도는 안 상함)
BSKY_FILE = os.path.join(_DIR, "issues_bsky.json")
_bsky_cache = {}         # cat → (다음 호출 가능 시각, 받은 시각, 지난 결과)
_bsky_last_call = 0.0


def _bsky_state():
    """{cat: {"ready":…, "at":…, "items":[…]}} — 데몬이 재시작해도 쉬는 시간이 살아남게."""
    if not _bsky_cache:
        for cat, v in (_load(BSKY_FILE, {}) or {}).items():
            if isinstance(v, dict):
                _bsky_cache[cat] = (v.get("ready", 0), v.get("at", 0), v.get("items") or [])
    return _bsky_cache


def _bsky_keep(cat, ready, at, items):
    _bsky_cache[cat] = (ready, at, items)
    _save(BSKY_FILE, {c: {"ready": r, "at": a, "items": i} for c, (r, a, i) in _bsky_cache.items()})


def src_bluesky(cat, query=None):
    """블루스카이 최근 24시간 인기글(키 없음). 좋아요 수로 반응 크기.
       검색어가 곧 분야 필터라 제목 단어 필터는 안 건다(영어 글이라 ECON_TOPIC 에 안 걸린다).
       query 를 주면(자가점검) 쉬는 시간을 무시하고 바로 부른다."""
    global _bsky_last_call
    import urllib.error
    if query is not None:
        return _bsky_search(cat, query)
    ready, got_at, last = _bsky_state().get(cat, (0, 0, []))
    fresh = [dict(x) for x in last] if (time.time() - got_at) < BSKY_STALE_H * 3600 else []
    if time.time() < ready or (time.time() - _bsky_last_call) < BSKY_MIN_GAP:
        return fresh
    qs = BSKY_QUERIES[cat]
    query = qs[_bsky_turn[cat] % len(qs)]
    _bsky_turn[cat] += 1
    _bsky_last_call = time.time()
    try:
        got = _bsky_search(cat, query)
    except urllib.error.HTTPError as e:
        # 막혔다고 가산점을 통째로 날리진 않는다 — 쉬는 동안은 마지막으로 받은 걸 계속 쓴다.
        _bsky_keep(cat, time.time() + (BSKY_BACKOFF if e.code in (403, 429) else BSKY_GAP), got_at, last)
        raise RuntimeError(f"HTTP {e.code} {_bsky_why(e)} — {int(BSKY_BACKOFF / 60)}분 쉼")
    _bsky_keep(cat, time.time() + BSKY_GAP, time.time(), got)
    return [dict(x) for x in got]


def _bsky_why(e):
    """403 본문에서 이유 한 조각. 그냥 'Forbidden' 만 남으면 다음에 또 봐도 원인을 모른다."""
    try:
        body = _strip_tags(e.read().decode("utf-8", "ignore"))[:120]
    except Exception:
        return ""
    return body.strip()


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


def src_mastodon(cat="ai", topic=AI_TOPIC):
    """마스토돈 인기글 중 분야에 걸린 것. 한 사람 의견이라 '잔잔'까지만.
       ★태그 타임라인(/timelines/tag/ai)은 쓰지 않는다 — 좋아요가 거의 전부 0 이라
         반응 크기를 못 잰다(2026-09-18 실측). 서버가 고른 인기글만 받아 분야어로 거른다."""
    out = []
    for p in json.loads(_get("https://mastodon.social/api/v1/trends/statuses?limit=40")):
        text = _strip_tags(p.get("content") or "")
        if len(text) < 25 or not topic.search(text):
            continue
        fav = int(p.get("favourites_count") or 0)
        if fav < 15:
            continue
        who = ((p.get("account") or {}).get("display_name")
               or (p.get("account") or {}).get("acct") or "").split("@")[0].strip()
        it = _item(cat, text[:140], 1, f"마스토돈 좋아요 {fav:,}", p.get("url") or "", lang="en")
        it["frame"] = f"마스토돈에서 {who or '어떤'} 이라는 유저가 쓴 개인 의견"
        out.append(it)
    return out


def src_papers(asked=False):
    """AI 논문 — 허깅페이스 데일리페이퍼(업보트로 강도)는 늘,
       arXiv cs.AI 최신은 호윤이 물어볼 때만(asked=True).
       ★arXiv 는 업보트가 없어 강도를 못 매긴다 — 전부 '잔잔'이라 자동 알림엔 안 뜨면서
         목록만 논문으로 덮는다. 그래서 자동 점검에선 뺐다(호윤 판단, 2026-09-18).
       읽어줄 때 '논문'이라고 밝히도록 frame 을 단다."""
    out, now = [], time.time()
    try:
        for r in json.loads(_get("https://huggingface.co/api/daily_papers?limit=30")):
            p = r.get("paper") or {}
            title, up = _strip_tags(p.get("title") or ""), int(p.get("upvotes") or 0)
            if not title:
                continue
            try:
                if (now - datetime.fromisoformat(
                        (r.get("publishedAt") or "").replace("Z", "+00:00")).timestamp()) > 3 * 86400:
                    continue
            except Exception:
                continue
            lv = 2 if up >= 30 else 1 if up >= 5 else 0
            if not lv:
                continue
            it = _item("ai", title, lv, f"허깅페이스 데일리페이퍼 추천 {up}",
                       f"https://huggingface.co/papers/{p.get('id')}", lang="en")
            it["frame"] = "AI 논문"
            it["lead"] = _strip_tags(p.get("summary") or "")[:600]
            out.append(it)
    except Exception as e:
        _FAILED.append(f"HF데일리페이퍼: {str(e)[:100]}")
    if not asked:
        return out
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(_get("http://export.arxiv.org/api/query?search_query=cat:cs.AI"
                                  "&sortBy=submittedDate&sortOrder=descending&max_results=25").encode())
        seen_t = {x["title"] for x in out}
        n_arxiv = 0
        for e in root.findall("{http://www.w3.org/2005/Atom}entry")[:25]:
            title = _strip_tags(e.findtext("{http://www.w3.org/2005/Atom}title", ""))
            # 업보트가 없어 강도를 못 매긴다 — 분야어에 걸린 것만, 전부 '잔잔'
            if not title or title in seen_t or not AI_TOPIC.search(title):
                continue
            link = e.findtext("{http://www.w3.org/2005/Atom}id", "")
            it = _item("ai", title, 1, "arXiv cs.AI 신규", link, lang="en")
            it["frame"] = "AI 논문"
            it["lead"] = _strip_tags(e.findtext("{http://www.w3.org/2005/Atom}summary", ""))[:600]
            out.append(it)
            n_arxiv += 1
            # arXiv 는 하루 수십 편 — 여기서 자른다. ★상한은 arXiv 것만 센다.
            #   전체(out)로 세면 데일리페이퍼가 먼저 자리를 다 채워 arXiv 가 1건만 들어왔다.
            if n_arxiv >= 8:
                break
    except Exception as e:
        _FAILED.append(f"arXiv: {str(e)[:100]}")
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


def src_lobsters(cat="ai", topic=AI_TOPIC):
    """Lobsters 1면 — 해커뉴스와 비슷한 개발자 커뮤니티. 점수가 곧 반응 크기.
       ★/t/ai.json(태그 페이지)이 아니라 hottest.json 을 받아 분야어로 거른다 —
         태그 페이지는 최고점이 27점이라 강도를 못 매긴다(2026-09-18 실측)."""
    out, cut = [], time.time() - 48 * 3600
    for x in json.loads(_get("https://lobste.rs/hottest.json")):
        title = _strip_tags(x.get("title") or "")
        if not topic.search(title + " " + (x.get("description_plain") or "")):
            continue
        try:
            if datetime.fromisoformat(x["created_at"]).timestamp() < cut:
                continue
        except Exception:
            pass
        pts = int(x.get("score") or 0)
        lv = 2 if pts >= 60 else 1 if pts >= 20 else 0
        if lv:
            out.append(_item(cat, title, lv, f"Lobsters {pts}점",
                             x.get("short_id_url") or x.get("comments_url") or "", lang="en"))
    return out


def src_github_trending(cat="ai", topic=AI_TOPIC):
    """GitHub 하루 트렌딩 — 오늘 붙은 별이 곧 반응 크기. 저장소 이름·설명이 분야어에 걸린 것만.
       ★마크업이 바뀌면 조용히 0건이 된다 — --sources 자가점검에 들어 있다."""
    out = []
    for row in _get("https://github.com/trending?since=daily").split('class="Box-row"')[1:]:
        # ★<a> 는 href 앞에 data-hydro-click 이 먼저 온다 — '<a href' 로 붙여 찾으면 통째로 0건이 된다
        repo = re.search(r'<h2[^>]*>\s*<a\b[^>]*?href="/([\w.-]+/[\w.-]+)"', row)
        star = re.search(r"([\d,]+)\s*stars\s*today", row)
        desc = re.search(r'<p[^>]*class="col-9[^"]*"[^>]*>\s*(.*?)\s*</p>', row, re.S)
        if not (repo and star):
            continue
        name, d = repo.group(1), _strip_tags(desc.group(1)) if desc else ""
        if not topic.search(name + " " + d):
            continue
        n = int(star.group(1).replace(",", ""))
        lv = 2 if n >= 1500 else 1 if n >= 500 else 0
        if lv:
            it = _item(cat, f"GitHub 트렌딩: {name} — {d[:80]}", lv, f"오늘 별 {n:,}",
                       f"https://github.com/{name}", lang="en")
            it["ko"] = f"깃허브 트렌딩에 {name.split('/')[-1]} 올라왔어 (오늘 별 {n:,}개)"
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
        _FAILED.append(f"{fn.__name__}: {str(e)[:100]}")   # 40자면 403 이유가 잘려 원인을 못 봤다
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
    for x in out:                       # 칼럼·질문형은 굵직이 아니면 잔잔으로 — '보통'에선 안 울린다
        if x["level"] < 3 and _opinion(x["title"]):
            x["level"] = 1
            x["why"] = x.get("why", "") + " · 칼럼/질문형"
    return out


# 칼럼·질문형 제목 — 사건이 아니라 의견이다.
# "주식 시장이 우리가 생각하는 것과 다른 걸 말하고 있을까?" 가 '보통'에서 울려 당황(2026-09-17).
OPINION = re.compile(
    r"(\?\s*$|？\s*$|^(why|how|is|are|should|what|can|could|will|would|do|does|did|here'?s|opinion)\b|"
    r"\b(opinion|analysis|column|commentary|explainer|op-ed)\b|"
    r"칼럼|사설|기고|시론|논단|기자수첩|데스크|분석\]|\[해설|까\s*$|나\s*$|는가\s*$)", re.I)


def _opinion(title):
    return bool(OPINION.search(_clean_title(title)))


# ═══════════════════════════════════════════════════════════════
#  수집 — 분야별
# ═══════════════════════════════════════════════════════════════
def collect_ai(asked=False):
    """asked=True = 호윤이 '뉴스' 라고 직접 물어본 것. '물어볼 때만' 소스까지 같이 본다."""
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
    items += _feeds("ai", asked)
    for g in _cluster(items):
        if not AI_TOPIC.search(g["title"]):
            continue
        n = len(g["sources"])
        lv = _level_from_cluster(n, bool(AI_EVENT.search(g["title"])), lv2=3, lv3=7)
        found.append({"cat": "ai", "level": lv, "title": g["title"],
                      "lang": g["items"][0].get("lang", "ko"), "why": f"{n}개 매체 보도",
                      "url": g["items"][0].get("link", ""), "tokens": sorted(g["tokens"]),
                      "alts": _alts(g)})
    found += _safe(src_geeknews)
    found += _safe(src_bluesky, "ai")
    found += _safe(src_mastodon, "ai", AI_TOPIC)
    found += _safe(src_lobsters, "ai", AI_TOPIC)
    found += _safe(src_github_trending, "ai", AI_TOPIC)
    found += _safe(src_papers, asked)
    found += _safe(src_hf_trending)
    found += _safe(src_github_releases)
    found += _safe(src_dcinside, "thesingularity", "mgallery/board", "ai", 150, 60, "디시 특이점갤", AI_TOPIC)
    found += _safe(src_clien, "ai", AI_TOPIC)
    return _merge_community(found)


def collect_econ(asked=False):
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
            up = pct > 0
            verb = "급등" if up else "급락"          # 화면 제목 — 짧게
            #  ★2026-09-22 호윤: *"1% 급등했어. 도 아니고 1% 올라갔어. 급등 이러니까 듣기 좀 그러네"*
            #  문턱(t2)만 넘으면 전부 '급등' 이라 1%대에도 급등이라 읽혔다. 귀로 듣는 말은 폭에 맞춰 고른다.
            #  t3(굵직한 단계) 넘을 때만 급등/급락, 그 아래는 그냥 올랐어/내렸어.
            if a >= t3 * 1.3:
                said = "급등했어" if up else "급락했어"
            elif a >= t3:
                said = "많이 올랐어" if up else "많이 빠졌어"
            else:
                said = "올랐어" if up else "내렸어"
            ko = f"{name}{_ga(name)} {a:.1f}% {said}"
            if sym == "KRW=X":
                verb = "원화 약세" if up else "원화 강세"
                ko = (f"원달러 환율이 {a:.1f}% 올라서 원화가 약해졌어" if up
                      else f"원달러 환율이 {a:.1f}% 내려서 원화가 강해졌어")
            found.append({"cat": "econ", "level": lv, "lang": "ko",
                          "title": f"{name} {pct:+.2f}% {verb}",
                          "ko": ko,
                          "why": "Yahoo 실측", "url": "",
                          # 같은 거래일·같은 종목은 한 사건 — 단계가 오를 때만 다시 알린다.
                          #  ★날짜는 '오늘'이 아니라 마지막 체결 시각(UTC)의 날짜. 오늘 날짜로 했더니 자정만 넘으면
                          #    같은 등락을 또 알렸다 — 9/18 코스피 +2.66% 를 9/19 00:01·9/20 00:00(주말)에 다시.
                          "key": f"mkt:{sym}:{_trade_day(r.get('time'))}",
                          "tokens": [sym.lower(), name]})
    except Exception:
        pass
    # 2) 뉴스 — 사건어 + 매체 수
    items = _news([("경제 OR 금리 OR 환율 OR 증시 when:1d", "ko"),
                   ("Fed OR stock market OR tariff OR recession when:1d", "en")])
    items += _feeds("econ", asked)
    for g in _cluster(items):
        n = len(g["sources"])
        ev = bool(ECON_EVENT.search(g["title"]))
        lv = _level_from_cluster(n, ev, lv2=4, lv3=8)
        if not ev and lv < 2:
            continue                       # 사건어도 없고 보도도 적으면 소음
        found.append({"cat": "econ", "level": lv, "title": g["title"],
                      "lang": g["items"][0].get("lang", "ko"), "why": f"{n}개 매체 보도",
                      "url": g["items"][0].get("link", ""), "tokens": sorted(g["tokens"]),
                      "alts": _alts(g)})
    # 디시는 분야 단어 필터 필수 — 없으면 '대통령 티어표.jpg' 같은 밈이 추천 100+ 로 올라온다(2026-09-15)
    found += _safe(src_dcinside, "neostock", "board", "econ", 150, 60, "디시 주식갤", ECON_TOPIC)
    found += _safe(src_dcinside, "stockus", "mgallery/board", "econ", 150, 60, "디시 미주갤", ECON_TOPIC)
    found += _safe(src_dcinside, "bitcoins_new1", "board", "econ", 150, 60, "디시 코인갤", ECON_TOPIC)
    found += _safe(src_clien)
    found += _safe(src_bluesky, "econ")
    found += _safe(src_mastodon, "econ", ECON_TOPIC)
    return _merge_community(found)


COLLECTORS = {"ai": collect_ai, "econ": collect_econ}


def _trade_day(ts):
    """시세의 마지막 체결 시각 → 거래일(UTC 날짜). 코스피 마감 15:30 KST·미장 마감 16:00 ET·코인/환율 하루 모두
       UTC 로 같은 날에 떨어진다. 시각이 없으면 오늘."""
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now().strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════
#  중복 제거 — 이미 알린 사건은 다시 안 알린다
#   · 뉴스·커뮤니티 = 같은 사건(_same_event)을 한 번 알렸으면(told) 매체 수·점수가 늘어도 다시 안 알린다.
#     ★예전엔 '강도가 오르면 다시' 였다 — 같은 기사가 2개→4개 매체, 해커뉴스 250→600점이 될 때마다 또 울렸다
#       (알림 기록 265건 중 같은 제목이 단계만 올라 다시 울린 것 11번: Bend·김학균·버핏·Astra 등).
#     기준 미달이라 안 알리고 '본 것'으로만 둔 건(told 없음) 예전처럼 강도가 오르면 그때 알린다.
#   · 시장 등락(mkt: 열쇠) = 같은 종목·거래일은 단계가 오를 때만 다시(+2% → +3.5% 는 새 소식이다).
#   · 커뮤니티 글(열쇠=주소)도 내용으로 비교한다 — 예전엔 주소만 봐서, 해커뉴스로 알린 걸
#     깃허브 트렌딩·허깅페이스로 또 알렸다(google/ax 9/22→9/23, Qwen Image 2.1 9/22 두 번).
# ═══════════════════════════════════════════════════════════════
def _is_mkt(x):
    return str(x.get("key") or "").startswith("mkt:")


def _alts(g):
    """묶음에 들어간 기사 제목들(앞 8개). 묶음의 대표 제목은 판마다 RSS 순서 따라 바뀐다 —
       대표 제목끼리만 비교하면 같은 묶음을 새 소식으로 볼 수 있어서, 겹치는 기사 제목이 하나라도 있으면 같은 사건."""
    return [_clean_title(x["title"]) for x in g["items"][:8]]


def _names(x):
    return {t for t in [x.get("title")] + list(x.get("alts") or []) if t}


def _filter_new(found, seen):
    now = time.time()
    seen[:] = [s for s in seen if now - s.get("t", 0) < SEEN_KEEP_H * 3600]
    fresh = []
    for it in sorted(found, key=lambda x: -x["level"]):
        # ★처음 걸린 하나만 보면 안 된다 — 옛날에 낮게 본 기록이 먼저 걸리면 '강도가 올랐다'로 착각해
        #   이미 굵직으로 알린 걸 매 바퀴 다시 알렸다(2026-09-17, Emerald AI 3연속). 걸린 것 중 최고 강도와 비교.
        mkt = _is_mkt(it)
        names = _names(it)
        prev, told = 0, False
        for s in seen + fresh:
            if s.get("cat") != it["cat"]:
                continue
            if mkt or _is_mkt(s):
                same = s.get("key") == it.get("key")
            else:
                same = ((it.get("key") and s.get("key") == it.get("key"))
                        or bool(names & _names(s))                                # 같은 기사가 들어 있다
                        or _same_event(it["title"], s.get("title"))
                        or (s.get("ko") and _same_event(it["title"], s["ko"])))   # 영어로 알린 걸 한국어 기사로
            if same:
                prev = max(prev, s.get("level", 0))
                told = told or bool(s.get("told"))
        if (told and not mkt) or prev >= it["level"]:
            continue
        fresh.append(it)
    return fresh


# ═══════════════════════════════════════════════════════════════
#  뜻으로 같은 사건 거르기 — 낱말 판정(_same_event)이 못 잇는 영어↔한국어·말 바꾼 기사 (2026-10-07)
#   알림 기록 재생에서 남은 중복 27건 대부분이 'OpenAI breached by researchers…' ↔ '앤트로픽 클로드로
#   오픈AI 뚫었다' 처럼 언어·표현만 다른 같은 사건이었다(호윤: "중복으로 가져오는 것도 많더라").
#   ★임베딩(embeddinggemma) 유사도만으로는 못 가른다 — 정답지 쌍을 재 보니 같은 사건 0.51~0.85,
#     다른 사건 최고 0.85('코스피 급등…반도체 강세' ↔ '뉴욕증시…반도체 강세', 같은 날 다른 시장).
#     어느 문턱이든 다른 소식을 같이 묻는다(0.80 에서 10쌍 잡고 5쌍 잘못 묶음).
#   → 임베딩은 '같은 사건일 수도 있는 짝' 고르기에만 쓰고(EMB_CAND — 같은 사건 쌍 최저 0.51 밑),
#     판정은 아스트라 → 클로드(구독 CLI)에게 묻는다(호윤: claude·gpt 로 풀 수 있는 건 그쪽 cli 로).
#     알릴 게 생긴 판에서, 가까운 짝이 있을 때만 묻는다. 둘 다 실패하면 거르지 않는다 —
#     중복 한 번 울리는 게 다른 소식을 묻는 것보다 낫다.
# ═══════════════════════════════════════════════════════════════
EMB_MODEL = "embeddinggemma:300m"
EMB_PREFIX = "task: sentence similarity | query: "
EMB_CAND = 0.45            # 이 이상 가까운 '알린 소식'이 있을 때만 판정을 묻는다
_EMB = {}                  # 제목 → 벡터(프로세스 안 캐시 — 본 것 수백 개를 매 판 다시 안 돌리게)


def _embed(titles):
    need = [t for t in dict.fromkeys(titles) if t not in _EMB]
    if need:
        import ollama
        vecs = ollama.Client(timeout=30).embed(model=EMB_MODEL, keep_alive="10m",
                                               input=[EMB_PREFIX + t for t in need])["embeddings"]
        _EMB.update(zip(need, vecs))
        while len(_EMB) > 3000:                           # 오래된 것부터 버린다(넣은 순서)
            _EMB.pop(next(iter(_EMB)))
    return [_EMB[t] for t in titles]


def _cos(a, b):
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


SAME_PROMPT = (
    "[새 소식] 각 줄이 [이미 알린 소식] 중 하나와 '같은 사건'인지 판정해라.\n"
    "같은 사건 = 같은 일(같은 발표·사고·수치·결정)을 다룬 기사. 언어(영어·한국어)·표현·매체가 달라도 같은 사건이다.\n"
    "다른 사건 = 같은 회사·같은 주제라도 다른 발표·다른 날의 일, 다른 나라 시장"
    "(예: 코스피 마감 ≠ 뉴욕증시 마감, 'GPT-6 Sol 출시' ≠ 'GPT-6 Astra 성능 분석'). 애매하면 다른 사건.\n"
    '파일은 건드리지 말고 JSON 하나만: {"same": ["N1", ...]}  (같은 사건인 새 소식 번호만, 없으면 [])\n')


def _ask_same(told_titles, cand_titles, log=print):
    """→ 같은 사건으로 판정된 후보 번호(0부터) 집합. 두뇌가 다 실패하면 None(= 거르지 않음)."""
    import debate
    prompt = (SAME_PROMPT + "\n[이미 알린 소식]\n"
              + "\n".join(f"T{i + 1}) {t}" for i, t in enumerate(told_titles))
              + "\n\n[새 소식]\n" + "\n".join(f"N{i + 1}) {t}" for i, t in enumerate(cand_titles)))
    for who, ask in (("아스트라", lambda p: debate.ask_astra(p, timeout=90)),
                     ("클로드", lambda p: debate.ask_claude(p, timeout=90))):
        try:
            out = ask(prompt)
            m = re.search(r"\{.*\}", out or "", re.S)
            same = json.loads(m.group(0)).get("same") if m else None
            if isinstance(same, list):
                return {int(s[1:]) - 1 for s in map(str, same)
                        if re.fullmatch(r"N\d+", s) and 0 < int(s[1:]) <= len(cand_titles)}
            log(f"[이슈] 같은 사건 판정 — {who} 답 형식이 이상함")
        except Exception as e:
            log(f"[이슈] 같은 사건 판정 — {who} 실패: {str(e)[:100]}")
    return None


def _meaning_dupes(hit, seen, log=print):
    """알릴 후보(hit) 중 최근 48시간 안에 '알린' 소식과 뜻으로 같은 사건 → 그 후보들의 id 집합.
       시장 등락(mkt:)은 열쇠로 이미 가린다 — 여기선 안 본다."""
    cands = [x for x in hit if not _is_mkt(x)]
    now = time.time()
    told = [s for s in seen if s.get("told") and not _is_mkt(s) and s.get("title")
            and now - s.get("t", 0) < SEEN_KEEP_H * 3600
            and cands and s.get("cat") == cands[0]["cat"]]
    if not cands or not told:
        return set()
    try:
        vc = _embed([_clean_title(x["title"]) for x in cands])
        vt = _embed([_clean_title(s["title"]) for s in told])
        near = {i: [j for j, b in enumerate(vt) if _cos(a, b) >= EMB_CAND] for i, a in enumerate(vc)}
        near = {i: js for i, js in near.items() if js}
    except Exception as e:                       # ollama 가 꺼져 있으면 — 최근 알린 것 전부와 비교해 묻는다
        log(f"[이슈] 뜻 비교 임베딩 실패({str(e)[:80]}) — 최근 알린 것 전부로 판정")
        near = {i: list(range(len(told))) for i in range(len(cands))}
    if not near:
        return set()
    js = sorted({j for v in near.values() for j in v}, key=lambda j: -told[j].get("t", 0))[:15]
    told_titles = [_clean_title(told[j]["title"]) + (f"  (한국어: {told[j]['ko']})" if told[j].get("ko")
                                                     else "") for j in js]
    order = sorted(near)
    same = _ask_same(told_titles, [_clean_title(cands[i]["title"]) for i in order], log)
    if not same:
        return set()
    dup = [cands[order[k]] for k in same]
    log(f"[이슈] 뜻으로 같은 사건 {len(dup)}건 거름 — " + " / ".join(x["title"][:40] for x in dup))
    return {id(x) for x in dup}


def _mark_seen(items, seen, told=()):
    """told = 실제로 알릴 항목들(id). 그건 다시 안 알린다(위 머리말)."""
    now = time.time()
    told = {id(x) for x in told}
    for it in items:
        row = {"cat": it["cat"], "level": it["level"], "key": it.get("key"),
               "tokens": it.get("tokens"), "title": it["title"], "t": now}
        if id(it) in told:
            row["told"] = True
            if it.get("alts"):             # 알린 것만 — 본 것 파일이 2분마다 통째로 다시 써지니 크기를 아낀다
                row["alts"] = it["alts"]
        seen.append(row)
        it["_seen"] = row                  # 알릴 때 붙는 한국어 한 줄(ko)을 나중에 여기에도 적으려고


# ═══════════════════════════════════════════════════════════════
#  기사 내용 — 알릴 것만 원문을 찾아가 요약문(og:description)을 뽑는다
#   구글뉴스 링크는 news.google.com 중간 주소라 batchexecute 로 원문 주소를 푼다(2026-09-17 실측 5/5).
#   기사 페이지의 og:description 이 곧 리드 문단이다(한겨레·경향·중앙·NPR 확인, openai.com 은 403).
#   실패하면 조용히 내용 없이 제목만 — 알림이 내용 때문에 막히면 안 된다.
# ═══════════════════════════════════════════════════════════════
def _resolve_google(link, timeout=8):
    import urllib.parse
    aid = link.split("/articles/")[1].split("?")[0]
    s = _get(f"https://news.google.com/rss/articles/{aid}", timeout)
    sg = re.search(r'data-n-a-sg="([^"]+)"', s)
    ts = re.search(r'data-n-a-ts="([^"]+)"', s)
    if not (sg and ts):
        return ""
    inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
                             None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             aid, int(ts.group(1)), sg.group(1)]
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]]))
    req = urllib.request.Request("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                                 data=body.encode(), headers={
                                     "User-Agent": BROWSER_UA,
                                     "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    arr = json.loads(raw.split(chr(10) + chr(10))[1])
    return json.loads(arr[0][2])[1]


def _lead(url, timeout=8):
    """기사 주소 → 앞부분 요약(최대 400자). 못 뽑으면 ""."""
    import html as _h
    if "news.google.com/rss/articles/" in url:
        url = _resolve_google(url, timeout)
    if not url.startswith("http"):
        return ""
    s = _get(url, timeout)
    for pat in (r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*content="([^"]+)"',
                r'<meta[^>]+content="([^"]+)"[^>]*(?:property|name)=["\'](?:og:description|description)["\']'):
        m = re.search(pat, s)
        if m and len(m.group(1)) >= 40:
            return _h.unescape(m.group(1)).strip()[:400]
    ps = [_strip_tags(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", s, re.S)]
    return " ".join(p for p in ps if len(p) > 60)[:400]


def _enrich(items):
    """알릴 항목에 lead(원문 앞부분)를 붙인다. 커뮤니티 글·실측 등락은 건너뜀. 동시에 받는다."""
    from concurrent.futures import ThreadPoolExecutor
    todo = [it for it in items if it.get("url") and not it.get("community")
            and not it.get("lead") and not it.get("detail")]

    def one(it):
        try:
            it["lead"] = _lead(it["url"])
        except Exception:
            pass
    if todo:
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(one, todo))


def _first_korean_sentence(t):
    if not t or len(re.findall(r"[가-힣]", t)) < 10:
        return ""
    return re.split(r"(?<=[다요음])\.\s", t)[0].rstrip(".") + "."


# ═══════════════════════════════════════════════════════════════
#  한국어로 — 로컬 LLM(사고 끔). ko = 제목 한 줄, detail = 내용 1~2문장
#  GPU 바쁘면 LLM 을 건너뛴다 — 원문이 한국어면 첫 문장을 내용으로 쓴다.
# ═══════════════════════════════════════════════════════════════
def _is_korean(t):
    """한글이 글자의 30% 이상이면 한국어로 친다 — "OpenAI 가 ~했대" 는 한국어, 영어 제목은 아님."""
    han = len(re.findall(r"[가-힣]", t or ""))
    return han >= 2 and han >= 0.3 * len(re.findall(r"[A-Za-z가-힣]", t or ""))


def _ko_prompt(todo):
    listing = "\n".join(f"{i}. 제목: {it['title']}"
                        + (f"\n   ※이건 {it['frame']}이다." if it.get("frame") else "")
                        + (f"\n   본문 앞부분: {it['lead']}" if it.get("lead") else "")
                        for i, it in enumerate(todo))
    return ("아래 뉴스마다 두 가지를 한국어로 써라.\n"
            "ko: 제목을 한 문장(35자 이내)으로. 반말로 끝내라(예: '~했대', '~나왔어').\n"
            "※ 표시가 있으면 그게 뭔지 ko 에서 드러내라 — 사실 보도가 아닌 걸 단정으로 읽으면 안 된다.\n"
            "  · 개인 의견: '누구누구라는 유저가 ~래' 처럼 누가 한 말인지 밝히고, "
            "끝에 '개인 의견이야' 를 붙여라(35자 넘어도 된다).\n"
            "  · 논문: '~라는 논문이 나왔어' 또는 '~주제 논문인데 ~라는 내용이야' 처럼 써라.\n"
            "detail: 본문 앞부분이 있으면 핵심 내용을 1~2문장(90자 이내)으로 — 숫자·누가·무엇을 우선. "
            "제목에 이미 있는 말을 되풀이하지 말고, 제목에 없는 정보(수치·배경·이유)를 써라. "
            "본문 앞부분이 없거나 더할 정보가 없으면 빈 문자열. 반말로.\n"
            "과장·추측 금지. 주어진 제목·본문에 없는 사실은 절대 쓰지 마라. "
            "제목이 질문이면 ko 도 질문으로 끝내라.\n"
            'JSON 만: {"items":[{"i":0,"ko":"...","detail":"..."}]}\n\n' + listing)


def _apply_ko(todo, raw):
    """모델 답(JSON 글)을 항목에 채운다. 앞뒤 군말·코드펜스가 붙어 와도 { } 만 떼어 읽는다."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return
    for row in json.loads(m.group()).get("items", []):
        i = int(row.get("i", -1))
        if not 0 <= i < len(todo):
            continue
        if (row.get("ko") or "").strip() and not todo[i].get("ko"):
            todo[i]["ko"] = row["ko"].strip()
        if (row.get("detail") or "").strip() and todo[i].get("lead"):
            todo[i]["detail"] = row["detail"].strip()


def _koreanize(items, busy=False):
    todo = [it for it in items if not it.get("ko") or (it.get("lead") and not it.get("detail"))]
    if not todo:
        return
    if busy:
        for it in todo:
            if it.get("lead") and not it.get("detail"):
                it["detail"] = _first_korean_sentence(it["lead"])
    else:
        try:
            import ollama
            r = ollama.Client(timeout=120).chat(
                model=SUMMARY_MODEL, format="json", think=False, keep_alive="2m",
                options={"temperature": 0, "num_predict": 2000},
                messages=[{"role": "user", "content": _ko_prompt(todo)}])
            _apply_ko(todo, r["message"]["content"])
        except Exception:
            pass
    #  ★영어 제목이 한국어 한 줄 없이 남으면 영어로 읽힌다(2026-09-23 호윤: "뉴스 긁어왔을때 영어로 말하는데").
    #    GPU 가 바빠 건너뛰었거나 로컬 모델이 빠뜨린 것만 구독 CLI(claude -p)로 한 번 더. 그래도 없으면 _speech 가 소리에서 뺀다.
    left = [it for it in todo if not it.get("ko") and not _is_korean(it["title"])]
    if left:
        try:
            from debate import ask_claude
            _apply_ko(left, ask_claude(_ko_prompt(left), timeout=120))
        except Exception as e:
            print(f"  [이슈 번역 CLI 실패] {str(e)[:80]}")
    _frame_fallback(todo)


def _frame_fallback(items):
    """모델이 출처를 안 밝혔거나 아예 안 돌았을 때의 마지막 방어.
       ★남의 개인 의견·논문이 사실 보도처럼 읽히는 건 그냥 틀린 것이라 파이썬이 못을 박는다."""
    for it in items:
        frame = it.get("frame")
        if not frame:
            continue
        ko = (it.get("ko") or "").strip()
        if not ko and not _is_korean(it["title"]):
            continue                            # 영어 원제로 한 줄을 지어내면 그게 영어로 읽힌다 — 비워 두고 소리에서 뺀다
        if frame == "AI 논문":
            if "논문" not in ko:
                it["ko"] = (f"{ko} — 이런 논문이 나왔어" if ko
                            else f"{it['title'][:60]} 라는 논문이 나왔어")
        else:                                   # 마스토돈 등 개인 의견
            who = re.sub(r"^마스토돈에서\s*|\s*이라는 유저가 쓴 개인 의견$", "", frame)
            if "유저" not in ko and "의견" not in ko:
                it["ko"] = f"{who} 라는 유저가 {ko or it['title'][:60]}"
            if "의견" not in it["ko"]:
                it["ko"] = _sentence(it["ko"]) + " 개인 의견이야."


# ═══════════════════════════════════════════════════════════════
#  방금 보여준 소식 — "그거 내용 봐봐" 가 가리키는 것
#   배너·음성으로 내보낸 항목을 파일에 적어둔다. 데몬(알림 스레드)과 콘솔이 다른
#   프로세스라 메모리로는 안 통해서 파일로 둔다. 알림 기록(LOG_FILE)과 따로인 이유는
#   '뉴스'·'알림 다시'(record=False)로 본 것도 "그거"에 걸려야 하기 때문.
# ═══════════════════════════════════════════════════════════════
SHOWN_FILE = os.path.join(_DIR, "issues_shown.json")


def _remember_shown(cat, items):
    try:
        _save(SHOWN_FILE, {"at": time.time(), "cat": cat,
                           "items": [{k: it.get(k) for k in
                                      ("title", "ko", "detail", "why", "url", "level", "lead")}
                                     for it in items[:5]]})
    except Exception:
        pass


def last_shown(max_age_h=6):
    """(항목들, 몇 분 전) — 없거나 오래됐으면 ([], None)."""
    d = _load(SHOWN_FILE, {})
    items = d.get("items") or []
    if not items:
        # 이 파일이 생기기 전에 알린 것들 — 알림 기록의 마지막 묶음으로 대신한다
        logs = _load(LOG_FILE, [])
        if not logs:
            return [], None
        last_at = logs[-1].get("at", "")
        rows = [r for r in logs[-5:] if r.get("at") == last_at]
        try:
            t = datetime.fromisoformat(last_at).timestamp()
        except Exception:
            return [], None
        d, items = {"at": t}, rows
    age_min = (time.time() - d.get("at", 0)) / 60
    if age_min > max_age_h * 60:
        return [], None
    return items, int(age_min)


def _korean_body(title, lead):
    """원문 앞부분이 영어면 한국어로 옮겨서 돌려준다(소리로 읽어주려면 필요).
       로컬 → 구독 CLI 순. 둘 다 실패하면 '' — 영어 원문을 그대로 두면 그게 영어로 읽힌다
       (2026-09-23 호윤: 한국어로 말하게). 원문은 링크로 본다."""
    if not lead:
        return ""
    if len(re.findall(r"[가-힣]", lead)) >= 20:
        return lead
    prompt = ("아래 기사의 앞부분을 한국어 3문장 이내로 옮겨라. 반말로. "
              "숫자·누가·무엇을 살리고, 주어진 글에 없는 사실은 절대 쓰지 마라. "
              "설명이나 머리말 없이 본문만.\n\n"
              f"제목: {title}\n본문 앞부분: {lead}")
    try:
        import ollama
        r = ollama.Client(timeout=120).chat(
            model=SUMMARY_MODEL, think=False, keep_alive="2m",
            options={"temperature": 0, "num_predict": 500},
            messages=[{"role": "user", "content": prompt}])
        out = (r["message"].get("content") or "").strip()
        if _is_korean(out):
            return out
    except Exception:
        pass
    try:
        from debate import ask_claude
        out = ask_claude(prompt, timeout=120).strip()
        if _is_korean(out):
            return out
    except Exception as e:
        print(f"  [내용 번역 CLI 실패] {str(e)[:80]}")
    return ""


def detail_of_last(idx=0, max_age_h=6):
    """방금 알린 소식의 내용을 길게. 원문 앞부분을 그 자리에서 받아온다(없으면 요약이라도)."""
    items, age = last_shown(max_age_h)
    if not items:
        return None
    it = items[idx] if idx < len(items) else items[0]
    body = it.get("lead") or ""
    if not body and it.get("url"):
        try:
            body = _lead(it["url"])
        except Exception:
            body = ""
    when = "방금" if (age or 0) < 10 else f"{age}분 전" if age < 120 else f"{age // 60}시간 전"
    head = it.get("ko") or it.get("title") or ""
    if not it.get("ko") and not _is_korean(head):          # 알릴 때 못 옮긴 영어 제목 — 지금 한 번 더
        tmp = {"title": head}
        _koreanize([tmp])
        head = tmp.get("ko") or ""
    raw = body
    body = _korean_body(it.get("title") or head, body)
    out = [f"{when} 알린 소식이야. {_sentence(head)}" if head
           else f"{when} 알린 해외 소식이야. 제목을 한국어로 못 옮겼어."]
    # 알림 때 붙였던 한 줄 요약과 원문 앞부분이 사실상 같은 말인 경우가 많다 — 긴 쪽만 남긴다
    summary = it.get("detail") or ""
    if summary and body and _similar(_tokens(summary), _tokens(body)):
        summary, body = ("", body) if len(body) >= len(summary) else (summary, "")
    if summary:
        out.append(summary)
    if body:
        out.append(body)
    if not it.get("detail") and not body:
        out.append("원문이 영어인데 한국어로 못 옮겼어. 링크로 봐야 할 것 같아." if raw
                   else "원문 앞부분을 못 가져왔어. 링크로 봐야 할 것 같아.")
    if it.get("title") and it["title"] != head:
        out.append(f"(원제: {it['title'][:100]})")     # 괄호 안은 소리로 안 읽는다 — 120자 넘으면 새므로 자른다
    if it.get("url"):
        out.append(it["url"])
    rest = [(x.get("ko") or x.get("title") or "")[:30] for x in items[1:3]
            if x.get("ko") or _is_korean(x.get("title"))]
    if rest:
        out.append(f"같이 알린 것: {', '.join(rest)}")
    return "\n".join(out)


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


_last_say = 0.0     # 마지막으로 소리 낸 시각 — 말걸기 창이 같은 대답을 또 읽지 않게
SAY_WAIT = 15       # 배너를 목소리 준비까지 붙잡아 두는 상한(초). 평소 합성은 3~6초(아야카 2~3초 + 형체)


def _say(text, on_show=None):
    """읽는다. on_show(배너 등)는 소리가 나기 직전에 같이 띄운다 — 한 번만.
       ★2026-10-07 호윤: "소식이 나오고 TTS가 나오거든? 급한게 아니니까 TTS랑 동시에 나오도록".
         예전엔 배너를 먼저 띄우고 합성을 시작해서 목소리가 몇 초~수십 초 뒤에 따라왔다.
       합성이 SAY_WAIT 를 넘기면 배너만 먼저(목소리는 준비되는 대로), 못 읽었으면(실패·Esc·줄이 밀려 버림)
       배너만이라도 띄운다 — 소식 자체가 사라지면 안 된다."""
    global _last_say
    _last_say = time.time()
    shown, lock = [], threading.Lock()

    def show():
        with lock:
            if shown:
                return
            shown.append(1)
        if on_show:
            try:
                on_show()
            except Exception as e:
                print(f"  [이슈 배너 오류] {e}")
    timer = threading.Timer(SAY_WAIT, show) if on_show else None
    if timer:
        timer.daemon = True
        timer.start()
    try:
        import voice
        #   뒷전 소식이라 줄이 밀려 있으면 버린다 — 지난 뉴스를 몇 분 뒤에 읽어주면 더 이상하다
        voice.Mouth().speak(text, drop_if_busy=True, on_start=show)
    except Exception as e:
        print(f"  [이슈 TTS 오류] {e}")
    finally:
        if timer:
            timer.cancel()
        show()


# ═══════════════════════════════════════════════════════════════
#  대화창에도 — 알린 소식을 데몬 메모리에 쌓아 두면 대화창(chat_win)이 몇 초마다 가져가 말풍선으로 그린다
#   (데몬 POST /news 의 feed). 데몬이 켜져 있는 동안의 것만 — 지난 건 '알림 기록' 으로 본다.
#   ★대화 기록(comet_history.json)엔 안 넣는다(2026-10-07):
#     · 2분마다 오는 알림이 모델에 넘기는 최근 40개를 밀어내 정작 대화를 잊는다
#     · 말걸기 Esc 취소(respond_cancelable)는 '그 턴 시작 뒤' 를 통째로 지운다 — 생각하는 사이 들어온 알림이
#       같이 지워지거나 사용자/코멧 번갈이 사이에 끼어 모델이 헷갈린다
#     · "그거 내용 봐봐" 는 issues_shown.json 을 본다 — 기록에 없어도 된다
# ═══════════════════════════════════════════════════════════════
FEED = []                   # [{id, cat, level, text}] — id 는 ms 시각(데몬을 다시 켜도 계속 커진다)
FEED_KEEP = 50
_feed_lock = threading.Lock()


def _post_feed(cat, items):
    top = items[0]
    lines = [f"소식 · {CAT_NAME.get(cat, cat)} · {LEVEL_NAME.get(top['level'], '')}  {datetime.now():%H:%M}"]
    for x in items[:5]:
        lines.append("· " + (x.get("ko") or x["title"]))
        if x.get("detail") and _is_korean(x["detail"]):
            lines.append("   " + x["detail"])
        if x.get("why"):
            lines.append("   " + x["why"][:60])
    if len(items) > 5:
        lines.append(f"· 그 밖에 {len(items) - 5}건")
    with _feed_lock:
        fid = int(time.time() * 1000)
        if FEED and fid <= FEED[-1]["id"]:
            fid = FEED[-1]["id"] + 1
        FEED.append({"id": fid, "cat": cat, "level": top["level"], "text": "\n".join(lines)})
        del FEED[:-FEED_KEEP]


def post_note(cat, text):
    """소식 말고 대화창에 띄울 글 — 텔레그램으로 오간 말(cat 'tg_me' · 'tg_ai', 10/14 호윤 「텔레그램에서 내가 무슨 말 했는지 안 보여」).
       대화 기록엔 안 넣는다(위 이유 그대로) — 대화창이 /news 로 같이 받아 말풍선으로만 그린다."""
    with _feed_lock:
        fid = int(time.time() * 1000)
        if FEED and fid <= FEED[-1]["id"]:
            fid = FEED[-1]["id"] + 1
        FEED.append({"id": fid, "cat": cat, "level": 2, "text": text})
        del FEED[:-FEED_KEEP]


def feed_after(after):
    """(after 보다 뒤에 쌓인 것들, 마지막 id). after=None 이면 처음 묻는 것 — 지난 건 안 주고 마지막 id 만."""
    with _feed_lock:
        last = FEED[-1]["id"] if FEED else 0
        new = [dict(x) for x in FEED if after is not None and x["id"] > after]
    return new, last


def _sentence(t):
    """끝맺음 정리 — 물음표는 살린다(지우면 질문 제목이 뜬금없는 단정으로 들렸다, 2026-09-17)."""
    t = re.sub(r"[.。…\s]+$", "", t or "")
    return t if t.endswith(("?", "!")) else t + "."


def _speech(name, items):
    """여러 건이면 '먼저 ~. 다음은 ~. 마지막으로 ~.' 로 이어 읽는다(최대 3건). 내용(detail)이 있으면 제목 뒤에.
       한국어 한 줄이 '~발표했어' 같은 문장이라 '~가 있고' 로 잇지 않는다."""
    def one(x):
        s = _sentence(x.get("ko") or x["title"])
        return s + (" " + _sentence(x["detail"]) if x.get("detail") and _is_korean(x["detail"]) else "")
    #  한국어 한 줄이 끝내 없는 영어 제목은 읽지 않는다 — 화면 배너에만(2026-09-23 호윤: 한국어로 말하게)
    said = [x for x in items if x.get("ko") or _is_korean(x["title"])][:3]
    lines = [one(x) for x in said]
    left = len(items) - len(said)
    head = "굵직한 " if items[0]["level"] == 3 else ""
    if not lines:
        return f"{name} 쪽에 {head}해외 소식 {len(items)}건 있어. 한국어로 못 옮겨서 화면에만 띄웠어."
    if len(items) == 1:
        return f"{name} 쪽에 {head}소식 있어. {lines[0]}"
    out = f"{name} 쪽에 {head}소식 {len(items)}건 있어. "
    if len(lines) == 1:
        out += lines[0]
    else:
        out += f"먼저 {lines[0]} 다음은 {lines[1]}"
        if len(lines) > 2:
            out += f" 마지막으로 {lines[2]}"
    if left:
        out += f" 나머지 {left}건은 화면에 있어."
    return out


def deliver(cat, items, *, speak=True, show=True, log=print, record=True):
    """record=False: '알림 다시'·'뉴스' 처럼 다시 보여주기만 할 때 — 알림 기록에 또 쌓지 않는다."""
    if not items:
        return
    items = sorted(items, key=lambda x: -x["level"])
    top = items[0]
    _remember_shown(cat, items)     # "그거 내용 봐봐" 가 가리킬 것 (record 여부와 무관하게 남긴다)
    name = CAT_NAME.get(cat, cat)
    line = top.get("ko") or top["title"]
    more = f" 외 {len(items) - 1}건" if len(items) > 1 else ""
    tag = f"COMET · {name} · {LEVEL_NAME[top['level']]}"
    body = (top["detail"][:90] + "\n" if top.get("detail") else "") + top.get("why", "") + more
    if len(items) > 1:
        body += "\n" + "\n".join("· " + (x.get("ko") or x["title"])[:48] for x in items[1:3])

    def appear():               # 눈에 보이는 쪽 — 목소리와 같은 순간에(_say 가 재생 직전에 부른다)
        if show:
            popup(tag, line, body, top["level"], sec=6 + 2 * top["level"])
        if record:              # 대화창에도 글로(게임 중 배너를 접어도) — '뉴스'·'알림 다시' 는 답으로 이미 보인다
            _post_feed(cat, items)
    if speak:
        _say(_speech(name, items), on_show=appear)
    else:
        appear()                # 조용한 시간 — 배너만, 기다릴 소리가 없다
    if not record:
        return
    logs = _load(LOG_FILE, [])
    for it in items:
        logs.append({"at": datetime.now().isoformat(timespec="seconds"), "cat": cat,
                     "level": it["level"], "title": it["title"], "ko": it.get("ko"),
                     "detail": it.get("detail"), "why": it.get("why"), "url": it.get("url")})
    _save(LOG_FILE, logs[-300:])
    log(f"[이슈] {name} {len(items)}건 알림 — {line}")


# ═══════════════════════════════════════════════════════════════
#  요청하면 다시 — '뉴스'(지금 뜬 것 모아보기) / '알림 다시'(최근 알린 것 다시 듣기)
#  둘 다 알림 기록·본 목록(seen)을 안 건드린다 — 자율 점검 스레드와 같은 파일을 동시에 쓰지 않으려고.
# ═══════════════════════════════════════════════════════════════
KO_MARKETS = {"^KS11", "^KQ11", "KRW=X"}


def _region_ok(it, lang):
    """lang='ko' 한국 소식만 / 'en' 해외만. 실측 등락은 종목으로 가른다(코스피·코스닥·환율은 한국)."""
    if not lang:
        return True
    key = it.get("key") or ""
    if key.startswith("mkt:"):
        return (key.split(":")[1] in KO_MARKETS) == (lang == "ko")
    return (it.get("lang") or "ko") == lang


def news_digest(min_level=2, per_cat=5, *, gpu_busy_fn=None, speak=True, show=True, log=print, lang=None):
    """이미 알린 것까지 포함해 지금 뜬 AI·경제 소식을 강도순으로. 화면용 글을 돌려주고, 분야별로 배너+음성.
       lang='ko'|'en' 이면 한국/해외 소식만."""
    busy, _ = gpu_busy_fn() if gpu_busy_fn else (False, "")
    picked = {}
    for cat, fn in COLLECTORS.items():
        try:
            # asked=True — 호윤이 직접 물어본 자리다. 자동 점검에서 뺀 소스(연합뉴스·arXiv)도 여기선 본다.
            found = [x for x in fn(asked=True) if x["level"] >= min_level and _region_ok(x, lang)]
        except Exception as e:
            log(f"[뉴스] {cat} 수집 실패: {e}")
            continue
        rows = _filter_new(found, [])[:per_cat]      # 빈 seen → 같은 사건끼리만 하나로 줄인다
        if rows:
            picked[cat] = rows
    if not picked:
        where = {"ko": "한국 ", "en": "해외 "}.get(lang, "")
        msg = f"지금은 볼만한 {where}AI·경제 소식이 없어."
        if speak:
            _say(msg)
        return msg
    allrows = [x for rows in picked.values() for x in rows]
    _enrich(allrows)
    _koreanize(allrows, busy=busy)
    text = []
    for cat, rows in picked.items():
        text.append(f"[{CAT_NAME[cat]}]")
        for x in rows:
            text.append(f"  {LEVEL_NAME[x['level']]} · {x.get('ko') or x['title']}  ({x.get('why', '')})")
            if x.get("detail"):
                text.append(f"      {x['detail']}")
    for cat, rows in picked.items():
        deliver(cat, rows, speak=speak, show=show and not busy, log=log, record=False)
    return "\n".join(text)


def replay(n=3, *, speak=True, show=True, log=print):
    """최근 알린 것 n건을 다시 배너+음성으로. 분야별로 묶어서."""
    rows, titles = [], set()
    for r in reversed(_load(LOG_FILE, [])):           # 같은 제목이 여러 번 기록돼 있어도 한 번만
        if r["title"] not in titles:
            titles.add(r["title"])
            rows.insert(0, r)
        if len(rows) >= n:
            break
    if not rows:
        return "아직 알린 기록 없음."
    by_cat = {}
    for r in reversed(rows):                          # 최근 것부터
        by_cat.setdefault(r["cat"], []).append(r)
    for cat, items in by_cat.items():
        deliver(cat, items, speak=speak, show=show, log=log, record=False)
    return "최근 알림 다시:\n" + "\n".join(
        f"  {r['at'][5:16]} [{CAT_NAME.get(r['cat'], r['cat'])}·{LEVEL_NAME.get(r['level'])}] "
        f"{r.get('ko') or r['title']}" for r in reversed(rows))


# ═══════════════════════════════════════════════════════════════
#  한 번 점검 — 자율모드 스레드가 주기적으로 부른다
# ═══════════════════════════════════════════════════════════════
_check_lock = threading.Lock()
BUSY = threading.Event()   # 소식 모으는 중 — 데몬 _work_now 가 보고 대화창 혜성을 켠다(10/16)


def check(modes, **kw):
    """modes = {"ai":"굵직", "econ":"보통"} → 알린 건수 dict.
       baseline=True: 지금 떠 있는 건 '본 것'으로만 등록(처음 켤 때 몰아서 쏟아지는 것 방지).
       alive(): 거짓이면 알리지 않는다 — 수집(~10초) 도중에 '소식 듣기'를 껐을 때. 본 것 등록은 그대로.
       ★한 번에 하나 — '알림 지금'과 2분 점검이 겹치면 둘 다 같은 본 것 파일을 읽고 따로 알려 같은 걸 두 번 울렸다."""
    with _check_lock:
        BUSY.set()
        try:
            return _check(modes, **kw)
        finally:
            BUSY.clear()               # 예외가 나도 꺼지게


def _check(modes, *, gpu_busy_fn=None, quiet_hours=None, log=print, dry=False, baseline=False, alive=None):
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
            _save(SEEN_FILE, seen)
            result[cat] = 0
            continue
        hit = [x for x in new if x["level"] >= need][:5]
        # 뜻으로 같은 사건(영어↔한국어·말 바꾼 기사)인지 — '본 것' 등록 전에 봐야 자기 자신과 비교하지 않는다
        dup = _meaning_dupes(hit, seen, log) if hit else set()
        _mark_seen(new, seen, told=hit)    # 기준 미달도 '봤음' — 나중에 강도 오르면 그때 알림
        hit = [x for x in hit if id(x) not in dup]   # 거른 것도 '알린 것'으로 남긴다 — 다음 판에 또 묻지 않게
        #  ★알리기 전에 저장한다. 예전엔 두 분야를 다 돈 뒤 맨 끝에 한 번만 저장해서, AI 를 알리고 경제를 도는 사이
        #    데몬을 껐다 켜면 AI 쪽 '본 것'이 통째로 날아가 다음 판에 같은 5건을 또 알렸다(10/01 15:26 → 15:31).
        _save(SEEN_FILE, seen)
        if not hit or (alive and not alive()):
            result[cat] = 0
            continue
        _enrich(hit)
        _koreanize(hit, busy=busy)
        for x in hit:                      # 한국어 한 줄도 본 것에 — 영어로 알린 사건이 한국어 기사로 다시 와도 알아보게
            if x.get("ko") and x.get("_seen") is not None:
                x["_seen"]["ko"] = x["ko"]
        _save(SEEN_FILE, seen)
        if alive and not alive():          # 내용·번역 받는 사이(수 초~수십 초)에 꺼졌으면 여기서도 멈춘다
            result[cat] = 0
            continue
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
    "마스토돈": lambda: json.loads(_get("https://mastodon.social/api/v1/trends/statuses?limit=40")),
    "Lobsters": lambda: json.loads(_get("https://lobste.rs/hottest.json")),
    "GitHub 트렌딩": lambda: src_github_trending("ai", re.compile(".")),
    # 자가점검은 '살아 있나'를 보는 것이라 물어볼 때만 쓰는 것(arXiv·연합뉴스)까지 다 켠다
    "논문(HF+arXiv)": lambda: src_papers(asked=True),
    "매체 RSS": lambda: _feeds("ai", True) + _feeds("econ", True),
    "허깅페이스": lambda: json.loads(_get("https://huggingface.co/api/trending?type=model&limit=10"))["recentlyTrending"],
    "GitHub": lambda: [r for r in GH_REPOS if "<entry>" in _get(f"https://github.com/{r}/releases.atom")],
    "디시 특이점갤": lambda: src_dcinside("thesingularity", "mgallery/board", "ai", 1, 0, "특갤"),
    "디시 주식갤": lambda: src_dcinside("neostock", "board", "econ", 1, 0, "주갤"),
    "클리앙": lambda: src_clien(topic=None),
}

# 소스 생존 하루 한 번(데몬 13시 업그레이드 제안 때) — {"date": 마지막 점검일, "sources": {이름: {ok: 마지막 성공일, err: 오류 한 줄, dead: 죽어 있음, told: 죽음 알림 보냄, back: 되살아남 알림 못 보냄}}}
SOURCE_HEALTH_FILE = os.path.join(_DIR, "logs", "source_health.json")
_health_lock = threading.Lock()


def check_sources(log=print, force=False):
    """SOURCES 를 다 불러 상태 파일에 남기고, 「전엔 됐는데 이번에 0건 · 오류」인 소스만 모아 텔레그램 한 통.
       이미 알린 소스는 되살아날 때까지 다시 안 알리고, 되살아나면 한 줄. 전부 실패면 네트워크 끊김으로 보고 안 알린다.
       하루 한 번(force 면 그래도) — 날짜가 같으면 그냥 돌아간다."""
    if not _health_lock.acquire(blocking=False):
        return None
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        st = _load(SOURCE_HEALTH_FILE, {})
        if not force and st.get("date") == today:
            return None
        srcs = st.setdefault("sources", {})
        fails = {}
        for name, fn in SOURCES.items():
            try:
                n = len(fn())
                err = "" if n else "0건"
            except Exception as e:
                err = str(e).strip().splitlines()[0][:100] if str(e).strip() else type(e).__name__
            if err:
                fails[name] = err
        st["date"] = today
        all_down = len(fails) == len(SOURCES)
        # dead = 지금 죽어 있음(점검 결과) · told = 죽음을 텔레그램으로 알렸음 · back = 되살아남 알림 아직 못 보냄
        #  ★알림 완료는 발송 성공 뒤에만 적는다 — 먼저 적으면 발송 실패 때 되살아날 때까지 영영 안 알린다
        for name in SOURCES:
            s = srcs.setdefault(name, {"ok": "", "err": "", "dead": False})
            s.setdefault("told", s.get("dead", False))   # told 없던 옛 파일 — 그땐 dead 가 '알렸음'이었다
            if name not in fails:
                if s.get("told"):
                    s["back"] = True
                s.update(ok=today, err="", dead=False, told=False)
                continue
            s["err"] = fails[name]
            if all_down:                  # 네트워크 끊김 — 죽었다고 치지 않는다
                continue
            if s.get("ok"):               # 한 번도 된 적 없는 소스는 '죽음' 아님
                s["dead"] = True
                if s.pop("back", False):  # 되살아남을 못 알린 채 또 죽음 — 호윤이 아는 건 여전히 '죽음'이라 그대로 둔다
                    s["told"] = True
        died = [n for n in SOURCES if srcs[n].get("dead") and not srcs[n].get("told")]
        back = [n for n in SOURCES if srcs[n].get("back")]
        os.makedirs(os.path.dirname(SOURCE_HEALTH_FILE), exist_ok=True)
        _save(SOURCE_HEALTH_FILE, st)
        if all_down:
            log(f"[이슈] 소스 점검 — {len(SOURCES)}개 전부 실패, 네트워크 끊김으로 보고 안 알림")
            return st
        log(f"[이슈] 소스 점검 — 실패 {len(fails)}/{len(SOURCES)} · 알릴 죽음 {len(died)} · 되살아남 {len(back)}")
        lines = []
        if died:
            lines.append(f"이슈 소스 {len(died)}개가 죽었어 (사이트 구조 바뀜 의심)")
            lines += [f"· {n} — 마지막 성공 {srcs[n]['ok']} · {srcs[n]['err']}" for n in died]
        lines += [f"· {n} 되살아남" for n in back]
        if lines:
            try:
                import notify
                r = notify.send("📡 " + "\n".join(lines), html_mode=False)
            except Exception as e:
                r = {"ok": False, "msg": str(e)}
            if r.get("ok"):
                for n in died:
                    srcs[n]["told"] = True
                for n in back:
                    srcs[n].pop("back", None)
                _save(SOURCE_HEALTH_FILE, st)
            else:                         # 못 보낸 건 그대로 남아 다음 점검 때 다시 보낸다
                log(f"[이슈] 소스 점검 텔레그램 실패 — 다음 점검 때 다시: {r.get('msg')}")
        return st
    finally:
        _health_lock.release()


def dead_sources():
    """알린 뒤 아직 안 살아난 소스 이름들(업그레이드 상태 한 줄용)."""
    st = _load(SOURCE_HEALTH_FILE, {})
    return [n for n, s in (st.get("sources") or {}).items() if s.get("dead")]


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
