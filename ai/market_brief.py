# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 일일 미국 증시 브리핑 (좁게: 지수·반도체·특징주)
#
#  목표 = 폰에서 열자마자 "오늘 장이 어땠고 뭐가 튀었나"가 보이는 브리핑.
#  ★모양은 코드가 고정한다(2026-10-07, 호윤: "오긴 오는데 한눈에 보이는 정보가 아니다").
#    예전엔 LLM 이 통째로 줄글을 써서 지수 숫자가 맨 아래, 해석은 벽 글, 모양은 매일 달랐다.
#    → 숫자 칸(지수·큰 종목·공포탐욕)은 코드가 채우고, LLM 은 JSON 으로 '해석 칸'만 채운다.
#      자세한 해석은 텔레그램 접힌 인용(blockquote expandable) 안 — 첫 화면은 숫자와 한 줄 판단만.
#    배치는 아스트라와 같이 잡았다: 지수 → 한 줄 판단 → 큰 움직임 순, 색 이모지 대신 ▲▼,
#    LLM 글엔 시세 숫자를 못 쓰게(근거에 없는 숫자가 섞인 줄은 버림), 깨지면 숫자만이라도 보낸다.
#  그라운딩 철학(환각 차단):
#   · 지수·종목 등락률 = Yahoo 실측, 코드가 박는다 → LLM 이 숫자를 지어낼 자리가 없다.
#   · 사건·인과 = 구글뉴스 RSS 중 '이번 정규장 개장 뒤' 기사만 → LLM 해석('~로 보임').
#
#  ★ 사건 소싱 이력:
#   (2026-06) DuckDuckGo 는 홈페이지·옛 블로그(stale)를 줘서 9월 옛 사건이 섞였다 → 뉴스 RSS.
#   (2026-10-07) '최근 2일' → '이번 세션 개장 뒤'로 좁힘. 아침 7시엔 전날 세션 마감 기사
#     ("나스닥 사상 최고 마감")도 2일 안이라, 오늘 숫자에 어제 이유가 붙었다.
#     검색어도 교체 — 마감 1시간 뒤(05시대) 실측: '미국 증시 마감' 이번 세션 0건,
#     'semiconductor stocks' 는 추천·비교 글뿐 / '뉴욕증시' 6건 · 'stock market today' 9건 ·
#     'chip stocks' 는 반도체 종목이 왜 빠졌는지가 제목에 나온다.
#  proactive 모드가 매일 미장 마감 후(한국 아침) 호출 → 텔레그램 푸시.
# ═══════════════════════════════════════════════════════════════
import re
import json
import time
from datetime import date, datetime, timedelta, timezone

import ollama
import prices
import web
import news

# 종합·해석은 신중하게(정확>속도) — comet.HEAVY_MODEL·NUM_CTX 와 같은 값(comet 은 무거워 import 안 함).
#  ★2026-10-07: 예전 'gemma4:26b'(Q4 18GB)를 num_ctx 없이 불러, ollama 가 문맥을 최대로 잡고
#    21.9GB 가 필요하다며 보조 모델 적재에서 죽었다(9/22·9/23 로그의 ctx_other 줄). 그래서 아침마다
#    로컬이 10분 간격으로 실패하다 'GPU 바쁨' 으로 판정된 뒤에야 클라우드로 나갔다(9/23 은 4시간 늦음).
#    comet 이 9/22 에 고친 것과 같은 처방: Q3(13GB) + num_ctx 고정.
BRIEF_MODEL = "huihui-gemma4-abliterated:26b-a4b-i1-q3_k_m"
NUM_CTX = 32768
_AC = ollama.Client(timeout=600)      # 2패스 — 얼어붙음 대비 긴 타임아웃

# 첫 화면에 이 순서 그대로 찍힌다.
#  ★prices.stock("^SOX") 는 지수 별칭에 없어서 '^' 를 떼고 SOX 종목으로 찾다가 실패했다
#    → 필라델피아 반도체가 매일 빠져 있었다(2026-10-07 발견). 지수는 Yahoo 심볼로 바로 조회한다.
INDICES = [("^GSPC", "S&P500"), ("^IXIC", "나스닥"), ("^DJI", "다우"),
           ("^SOX", "필라델피아 반도체")]

# 좁은 범위 = 저 정리글의 단골(반도체·AI·메가캡)
MOVERS = [("NVDA", "엔비디아"), ("AMD", "AMD"), ("TSM", "TSMC"), ("MU", "마이크론"),
          ("AVGO", "브로드컴"), ("INTC", "인텔"), ("QCOM", "퀄컴"), ("ARM", "ARM"),
          ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("AMZN", "아마존"),
          ("TSLA", "테슬라"), ("GOOGL", "알파벳"), ("META", "메타")]
# 업종 — 종목 줄 끝에 '📈 +3.67% 브로드컴 · 반도체' 처럼 붙는다(호윤 요청 10/07). 굵게 세 갈래만:
#  더 잘게(메모리·파운드리…) 나누면 폰 한 줄을 넘고, 한눈에 '반도체가 빠졌나'를 보려는 목적엔 이걸로 충분.
SECTOR = {"NVDA": "반도체", "AMD": "반도체", "TSM": "반도체", "MU": "반도체", "AVGO": "반도체",
          "INTC": "반도체", "QCOM": "반도체", "ARM": "반도체",
          "AAPL": "빅테크", "MSFT": "빅테크", "AMZN": "빅테크", "GOOGL": "빅테크", "META": "빅테크",
          "TSLA": "전기차"}
# 영문 기사에서 종목을 알아볼 이름 — '종목별 이유'는 이번 장 기사에 그 종목이 실제로 나올 때만 받는다
MOVER_EN = {"NVDA": "Nvidia", "AMD": "Advanced Micro", "TSM": "Taiwan Semiconductor",
            "MU": "Micron", "AVGO": "Broadcom", "INTC": "Intel", "QCOM": "Qualcomm",
            "ARM": "Arm Holdings", "AAPL": "Apple", "MSFT": "Microsoft", "AMZN": "Amazon",
            "TSLA": "Tesla", "GOOGL": "Google", "META": "Meta"}

# 사건 소싱 = 구글뉴스 RSS, 이번 정규장 개장 뒤 기사만(_fresh_news)
NEWS_QUERIES = [("뉴욕증시", "ko"), ("뉴욕증시 마감", "ko"),
                ("stock market today", "en"), ("chip stocks", "en")]
FRESH_DAYS = 2                                # 세션 시각을 못 구했을 때만 쓰는 예비 기준
DEEP_K = 4                                    # 상위 헤드라인 N개는 본문까지 긁어 해석 깊이 확보
WEB_FALLBACK = ("뉴욕증시 마감 특징주", "kr-ko")   # 뉴스가 빈약할 때만 보조

BIG_MOVE = 1.5        # 첫 화면 '크게 움직인 종목' 기준(% 이상). 그런 게 3개 미만이면 큰 순서로 3개
MAX_MOVERS = 5
TG_MAX = 3900         # notify.send 가 4000자에서 자른다 — 태그 중간에서 잘리지 않게 여기서 먼저 맞춘다
# ★호윤은 폰(S26+)으로 본다 — 텔레그램 말풍선 한 줄 = 한글 20자 안팎. 첫 화면 줄은 안 접히게 짜고,
#   모델 칸도 이 폭에 맞춘다(summary 한 줄, 나머지는 두 줄 안). 프롬프트 목표를 HARD 만큼 넘치면 버린다.
HARD = {"summary": 26, "point": 40, "mover": 32, "mood": 60}
LINE_UNITS = 40       # 폰 한 줄 폭 — 한글 1자 = 2, 영문·숫자 1자 = 1 로 센다(한글 20자 ≈ 영문 40자)

_WEEK = "월화수목금토일"
_FG_KO = {"extreme fear": "극단 공포", "fear": "공포", "neutral": "중립",
          "greed": "탐욕", "extreme greed": "극단 탐욕"}
# CNN 세부지표 이름(prices) — 폰 한 줄에 들어가게 브리핑에서만 줄여 부른다
_FG_SHORT = {"시장 폭(상승·하락)": "시장 폭", "정크본드 수요": "정크본드", "안전자산 수요": "안전자산",
             "풋/콜 옵션비율": "풋콜 비율", "변동성(VIX)": "VIX"}


def _strip_think(t):
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    t = re.sub(r"^.*?</think>", "", t, flags=re.S)
    return t.strip()


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cut(s, units):
    """폰 폭(units)에 맞게 자르고 '…'. 한글·이모지는 2칸, 나머지는 1칸으로 센다 —
       글자 수로 자르면 영어 제목이 'WSJ Stock Market Toda…' 처럼 너무 일찍 잘린다."""
    out, used = "", 0
    for ch in s or "":
        w = 2 if ord(ch) >= 0x1100 else 1
        if used + w > units:
            return out.rstrip() + "…"
        out, used = out + ch, used + w
    return out


def _mark(p):
    """등락 이모지 — 📈(빨강)=오름 · 📉(파랑)=내림. 텔레그램은 글자색을 못 써서 이모지 색으로
       한국식 빨강·파랑을 낸다(호윤 요청 2026-10-07 — 처음엔 ▲▼ 였다)."""
    if not p:
        return "➖"
    return "📈" if p > 0 else "📉"


def _pct(p):
    return "—" if p is None else f"{p:+.2f}%"


def _row(name, p, width=6, tag="", strong=False, strong_tag=False):
    """폰 한 줄 — '📈 +3.67% 브로드컴 · 반도체'. 숫자를 이름 앞 고정폭(<code>)에 둬서 줄끼리 숫자 열을
       맞춘다. 폰 글꼴은 글자마다 폭이 달라 이름 뒤에 숫자를 두면 띄어쓰기로는 절대 안 맞는다(호윤 10/07).
       width = 이 메시지에서 제일 긴 등락 글자 수 — 짧은 건 앞을 띄워 소수점 자리를 맞춘다.
       tag = 줄 끝 꼬리표(종목은 업종, 지수는 '52주 최고 종가'). strong/strong_tag = 이름/꼬리표 굵게.
       ※텔레그램은 <code> 를 굵게로 감싸지도, 안에 굵게를 넣지도 못한다(엔티티 규칙) —
         그래서 '중요한 건 진하게'(호윤 10/07)는 숫자 대신 이름·꼬리표에 건다."""
    b = lambda s, on: f"<b>{_esc(s)}</b>" if on else _esc(s)
    return (f"{_mark(p)} <code>{_pct(p).rjust(width)}</code> {b(name, strong)}"
            + (f" · {b(tag, strong_tag)}" if tag else ""))


# ── 세션: 지금 다루는 게 어느 날 정규장인가 ─────────────────────────
def _ny_offset(d):
    """뉴욕의 UTC 오프셋(초) — 서머타임 = 3월 둘째 일요일 ~ 11월 첫 일요일.
       윈도우 파이썬엔 tzdata 가 없어 zoneinfo 를 못 쓴다. Yahoo 의 gmtoffset 은 '지금' 오프셋이라
       서머타임이 끝난 직후 주말엔 금요일 장 개장이 한 시간 밀려 계산된다(아스트라 검토)."""
    def sunday(month, nth):
        first = date(d.year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    return -4 * 3600 if sunday(3, 2) <= d < sunday(11, 1) else -5 * 3600


def _session(t):
    """마지막 체결 시각 t → 그 정규장 {date, open_ts, close_ts, closed}. t 가 없으면 None.
       마지막 체결의 뉴욕 날짜가 그 세션의 날이다 — 주말 아침엔 금요일."""
    if not t:
        return None
    d = datetime.fromtimestamp(t + _ny_offset(datetime.fromtimestamp(t, tz=timezone.utc).date()),
                               tz=timezone.utc).date()
    base = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - _ny_offset(d)
    open_ts, close_ts = base + 9 * 3600 + 30 * 60, base + 16 * 3600
    now = time.time()
    # 조기 폐장(13시)도 잡게 — 마지막 체결이 20분 넘게 멈춰 있으면 끝난 장으로 본다
    return {"date": d, "open_ts": open_ts, "close_ts": close_ts,
            "closed": now >= close_ts or now - t > 20 * 60}


def _when(s):
    d = s["date"]
    return f"{d.month}/{d.day}({_WEEK[d.weekday()]}) " + ("마감" if s["closed"] else "장중")


def _year_high(sym, s):
    """그 세션 종가가 52주(1년) 최고 종가인가. 오늘 봉은 가격이 아니라 날짜로 가려낸다 —
       가격이 비슷하다고 빼면 어제 봉을 오늘로 착각해 신고가가 잘못 붙는다(아스트라 검토).
       장중이면 아직 종가가 아니므로 판정하지 않는다."""
    if not s or not s["closed"]:
        return False
    j = prices._get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                         "?interval=1d&range=1y")
    res = j["chart"]["result"][0]
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    off = _ny_offset(s["date"])          # 일봉 시각은 개장(09:30)이라 한 시간 차이로 날짜가 안 바뀐다
    today, prior = None, []
    for ts, c in zip(res.get("timestamp") or [], quote.get("close") or []):
        if not c:
            continue
        d = datetime.fromtimestamp(ts + off, tz=timezone.utc).date()
        if d == s["date"]:
            today = c
        elif d < s["date"]:
            prior.append(c)
    return bool(today and prior) and today >= max(prior)


# ── 사건: 이번 정규장 기사만 ─────────────────────────────────────
def _fresh_news(session=None):
    """개장 뒤 ~ 마감 12시간 뒤에 나온 기사만. 아래쪽만 막으면 주말 아침엔 주말 기사까지
       금요일 장의 이유로 들어온다(아스트라 검토). 세션을 못 구했으면 최근 FRESH_DAYS 일."""
    if session:
        lo, hi = session["open_ts"], session["close_ts"] + 12 * 3600
    else:
        lo, hi = time.time() - FRESH_DAYS * 86400, time.time() + 3600
    items, seen = [], set()
    for kw, lang in NEWS_QUERIES:
        try:
            for it in news.fetch_keyword(kw, lang=lang, limit=12):
                if not lo <= it.get("_ts", 0) <= hi:    # 지난 세션·주말·오래된 기사 = 제외
                    continue
                key = (it.get("title") or "")[:48]
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(it)
        except Exception:
            pass
    items.sort(key=lambda x: x.get("_ts", 0), reverse=True)
    # 18건 — 14건으로 자르니 개장 직후의 종목 이유 기사('Intel Shares Buck Wider Trend')가 밀려났다
    return items[:18]


def _clean_title(t):
    """헤드라인에서 ' - 매체명' 꼬리·따옴표·괄호 제거 → 검색어로."""
    t = (t or "").split(" - ")[0].split(" | ")[0]
    return re.sub(r'["“”\[\]]', "", t).strip()


def _same_story(title, text):
    """긁어 온 본문이 그 헤드라인의 기사인가 — 제목 낱말(한글 2자+·영문 4자+)이 본문에 절반 이상.
       검색 결과 중 '제일 긴 본문'을 그냥 붙이면 다른 날·다른 사건 기사가 붙을 수 있다(아스트라 검토)."""
    words = {w.lower() for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", title or "")}
    body = (text or "").lower()
    return bool(words) and sum(w in body for w in words) >= max(2, len(words) // 2)


def _deepen(news_items):
    """상위 DEEP_K개 헤드라인을 web 검색해 진짜 기사 본문을 it['body']에 붙인다.
       (구글뉴스 링크는 리다이렉트라 본문이 안 나옴 → 헤드라인 검색 우회.)
       해석 깊이는 헤드라인이 아니라 본문에서 나온다."""
    picked, seen_pre = 0, set()
    for it in news_items:
        if picked >= DEEP_K:
            break
        title = it.get("title") or ""
        pre = title[:10]
        if not pre or pre in seen_pre:        # 비슷한 제목(같은 사건) 중복 회피
            continue
        seen_pre.add(pre)
        kl = "kr-ko" if it.get("lang") == "ko" else "us-en"
        try:
            res = web.research(_clean_title(title), n_sources=3, kl=kl)
            best = ""
            for s in res.get("sources", []):
                text = s.get("text") or ""
                if s.get("readable") and len(text) > len(best) \
                        and _same_story(_clean_title(title), text):
                    best = text
            if best:
                it["body"] = best[:900]
                picked += 1
        except Exception:
            pass
    return news_items


# ── 수집: 그라운딩 숫자(Yahoo·CNN) + 이번 세션 사건(news, 폴백 web) ─────
def gather():
    """→ {session, indices, movers, news, web, fg}. 숫자는 전부 실측, LLM 안 거침."""
    indices, movers = [], []
    for sym, name in INDICES:
        r = prices._yahoo(sym, "지수")
        if r.get("ok") and r.get("change_pct") is not None:
            indices.append({"sym": sym, "name": name, "pct": r["change_pct"],
                            "time": r.get("time"), "high": False})
    for sym, name in MOVERS:
        r = prices._yahoo(sym, "주가")
        if r.get("ok") and r.get("change_pct") is not None:
            movers.append({"sym": sym, "name": name, "pct": r["change_pct"], "time": r.get("time")})
    # 세션 = S&P500(없으면 처음 잡힌 시세)의 마지막 체결 날. 다른 날 값(멈춘 시세)은 한 통에 안 섞는다
    first = next((x for x in indices + movers if x.get("time")), None)
    session = _session(first["time"]) if first else None
    if session:
        same = lambda x: not x.get("time") or _session(x["time"])["date"] == session["date"]
        indices = [x for x in indices if same(x)]
        movers = [x for x in movers if same(x)]
        for r in indices:
            try:
                r["high"] = _year_high(r["sym"], session)
            except Exception:
                pass
    news_items = _deepen(_fresh_news(session))
    web_sources = []
    if len(news_items) < 3:                  # 이번 세션 뉴스가 빈약하면 웹 보조(날짜 미확인)
        try:
            res = web.research(WEB_FALLBACK[0], n_sources=4, kl=WEB_FALLBACK[1])
            if res.get("ok"):
                web_sources = res.get("sources", [])
        except Exception:
            pass
    return {"session": session, "indices": indices, "movers": movers,
            "news": news_items, "web": web_sources,
            "fg": prices.fear_greed("")}    # CNN 미장 군중심리(종합+세부+전날)


def _pick_movers(movers):
    """첫 화면에 올릴 종목 — BIG_MOVE% 이상 움직인 것(최대 MAX_MOVERS), 모자라면 큰 순서로 3개."""
    by = sorted(movers, key=lambda m: -abs(m["pct"]))
    big = [m for m in by if abs(m["pct"]) >= BIG_MOVE][:MAX_MOVERS]
    return big if len(big) >= 3 else by[:3]


def _evidence(data, today):
    s = data.get("session")
    ev = [f"[오늘(한국): {today} · 다루는 장: 미국 정규장 "
          f"{_when(s) if s else '(날짜 미확인)'}. 이 세션 얘기만 써라]",
          "",
          "[지수 — Yahoo 실측]",
          " · ".join(f"{r['name']} {r['pct']:+.2f}%" + (" (종가 기준 52주 최고)" if r["high"] else "")
                     for r in data["indices"]) or "(지수 수집 실패)",
          "",
          "[종목 — Yahoo 실측]",
          " · ".join(f"{m['name']}({m['sym']}·{SECTOR.get(m['sym'], '')}) {m['pct']:+.2f}%"
                     for m in data["movers"])
          or "(종목 수집 실패)"]
    picks = _pick_movers(data["movers"])
    if picks:
        ev += ["", "[크게 움직인 종목 — movers 칸에서 이유를 찾을 대상]",
               " · ".join(m["name"] for m in picks)]
    fg = data.get("fg")
    if fg and fg.get("ok"):
        ev += ["",
               "[시장 군중심리 — CNN Fear&Greed(역발상 신호). mood 칸의 재료]",
               prices.fmt(fg) + (f" · 전날 {fg['prev_close']}" if fg.get("prev_close") is not None
                                 else "")]
    ev += ["",
           "[이번 세션 기사 — 구글뉴스, 이번 장 개장 뒤 것만, 최신순(한국시각). 이것만 이유의 근거]"]
    if data["news"]:
        for it in data["news"]:
            ts = it.get("_ts", 0)
            d = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "??"
            ev.append(f"({d}) {it.get('title','')} — {it.get('source','')}")
            body = it.get("body")
            if body:        # 상위 기사 = 본문(해석 깊이의 핵심). 메뉴/네비 잡음 섞일 수 있음.
                ev.append(f"   [본문] {body}")
            else:
                sm = (it.get("summary") or "").strip()
                if sm:
                    ev.append(f"   {sm[:160]}")
    else:
        ev.append("(이번 세션 기사 없음 — points·movers 는 비워라)")
    if data["web"]:
        ev.append("")
        ev.append("[보조 웹 — 날짜 미확인, 참고만(단정 근거로 쓰지 마라)]")
        for s in data["web"][:4]:
            ev.append(f"· {s.get('title','')}: {(s.get('text') or s.get('snippet') or '')[:200]}")
    return "\n".join(ev)


SYNTH_SYS = (
    "너는 미국 증시 마감 브리핑의 '해석 칸'만 채운다. 지수·종목 숫자와 화면 모양은 코드가 따로 만든다.\n"
    "[근거]만 보고 아래 JSON 하나만 출력해라. 앞뒤 설명·코드펜스 금지, 마크다운은 points 의 ** 표시만.\n"
    '{"summary": "...", "points": ["...", "..."], "movers": {"종목이름": "..."}, "mood": "..."}\n'
    "읽는 사람은 폰으로 본다 — 한 줄에 한글 20자 안팎이 들어간다. 글자 수를 꼭 지켜라.\n"
    "- summary: 오늘 장 성격, 폰 한 줄 = 20자 이내. 예) 금리 하락에 기술주 최고치\n"
    "- points: 오늘 장을 움직인 이유·흐름 최대 3개, 각 20자 이내(폰 한 줄), 하나에 한 가지. "
    "이번 세션 기사에 있는 것만. 각 줄에서 제일 중요한 말 한 군데만 **두 별표**로 감싸라"
    "(화면에서 굵게 된다). 예) **국채금리 하락**에 기술주 강세\n"
    "- movers: [크게 움직인 종목] 중 이번 세션 기사에 그 종목 이름이 직접 나오고 움직인 이유가 적힌 것만 "
    '{"종목이름": "이유 18자 이내"}. \'업종 흐름에 동반\' 같은 추측은 넣지 마라. 하나도 없으면 {}.\n'
    "- mood: 공포·탐욕 세부지표가 '뜻하는' 분위기, 36자 이내(폰 두 줄). 어느 지표가 극단인지 나열은 코드가 "
    "따로 붙이니 반복하지 말고 해석만(예: '겉은 중립, 속은 소수 대형주 쏠림'). "
    "극단 공포=과매도·반등 기대, 극단 탐욕=과열 경계 맥락. 단정하지 말고 분위기로.\n"
    "[철칙]\n"
    "- 등락률·지수값·가격·% 같은 시세 숫자를 쓰지 마라. 숫자는 코드가 옆에 붙인다.\n"
    "- 이번 세션 기사에 없는 사건·인과는 쓰지 마라. 전망·추천·종목 비교 글('어느 주식이 나을까', "
    "'목표가', '사야 할 종목')은 오늘 등락의 이유가 아니다.\n"
    "- 사실은 기사대로, 네 추론은 '~로 보임'으로 끝내라. 과장 형용사·감탄 금지, 담백하게.\n"
    "- 말투는 기사 제목 같은 짧은 개조식(예: '금리 하락에 기술주 강세'). '~습니다'·'~했어' 어미 금지.\n"
    "- 근거가 빈약하면 points·movers 를 줄이거나 비워라. 지어내지 마라.\n"
    "- HTML·이모지·줄바꿈 금지(points 의 ** 표시만 예외).")

AUDIT_SYS = (
    "너는 냉정한 사실 감사관이다. [초안 JSON]을 [근거]와 대조해 고친 JSON 하나만 낸다(같은 키, 설명 금지).\n"
    "- 근거의 '이번 세션 기사'에 없는 사건·인과는 지운다. 전망·추천 글을 이유로 쓴 줄도 지운다.\n"
    "- 단정한 인과('때문에')는 '~영향으로 보임'으로 낮춘다.\n"
    "- 등락률·지수값 같은 시세 숫자는 지운다(코드가 따로 붙인다).\n"
    "- 글자 수(summary 20 · points 각 20 · movers 각 18 · mood 36)를 넘으면 줄인다 — 폰 한 줄이 한글 20자다.\n"
    "- points 의 **중요한 말** 표시는 지우지 말고 둔다(줄마다 한 군데).")


# ── 바쁠 때 우회하는 두뇌 = 호윤 구독 CLI(클로드 → 아스트라) ─────
#  API 키가 아니라 구독 CLI 를 부른다(debate.py 와 같은 통로) = 추가 과금 0, GPU 0.
#  ★로컬은 2패스(종합 → 사실감사)인데 감사 패스는 '로컬 모델이 지어내는 것'을 잡으려고
#    둔 것이다. 클로드·아스트라는 한 번 부르는 데 30초~1분씩 걸리므로 감사 규칙을 종합 지침에
#    얹어 1패스로 끝낸다. 숫자는 어차피 코드가 박는다.
CLOUD_SYS = (SYNTH_SYS + "\n[쓰고 나서 스스로 감사]\n"
             + "\n".join(AUDIT_SYS.splitlines()[1:])
             + "\n감사를 반영한 최종 JSON 하나만 출력.")


def _parse(text):
    """모델 답 → dict. 코드펜스·앞뒤 말이 붙어도 첫 '{' 부터 마지막 '}' 까지 읽는다. 실패 None."""
    t = _strip_think(text)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(t[a:b + 1])
    except Exception:
        return None
    return d if isinstance(d, dict) else None


def _cloud_brief(evidence, log=None):
    """구독 CLI로 해석 칸 생성 → (dict 또는 None, 두뇌이름).
       둘 다 응답 자체가 없으면 (None, ''). 답은 왔는데 JSON 이 아니면 다음 두뇌로 넘기고,
       끝까지 안 되면 (None, 마지막으로 답한 두뇌) — 그때는 숫자만이라도 보낸다."""
    import debate                       # ask_claude / ask_astra (ANTHROPIC_API_KEY 제거된 자식 환경)
    prompt = CLOUD_SYS + "\n\n[근거]\n" + evidence
    answered = ""
    for who, ask in (("클로드", debate.ask_claude), ("아스트라", debate.ask_astra)):
        try:
            out = ask(prompt)
        except Exception as e:
            if log:
                log(f"[brief] {who} 실패: {str(e)[:150]}")
            continue
        d = _parse(out)
        if d is not None:
            return d, who
        answered = who
        if log:
            log(f"[brief] {who} 답이 JSON 이 아님 — 다음 두뇌로")
    return None, answered


# 사고(thinking) 끄기 — analyst._chat 과 같은 처방(작업 유의사항 '모델 호출' 첫 항목).
#  gemma4 는 사고를 따로 세고 num_predict 를 사고+본문이 나눠 쓴다 → 사고가 다 먹으면 본문 0자.
_NO_THINK = set()


def _chat(**kw):
    """_AC.chat + think=False. think 인자를 거부하는 모델이면 그 모델만 빼고 재시도."""
    if kw.get("model") not in _NO_THINK:
        kw.setdefault("think", False)
    try:
        return _AC.chat(**kw)
    except Exception as e:
        if "think" in kw and "think" in str(e).lower():
            _NO_THINK.add(kw["model"])
            kw.pop("think")
            return _AC.chat(**kw)
        raise


def _local_brief(model, evidence):
    """로컬 2패스(종합 → 사실감사), 둘 다 JSON 모드. → (dict 또는 None, 오류문구 또는 None)."""
    try:
        r1 = _chat(model=model, keep_alive="2m", format="json",
                   options={"temperature": 0.2, "num_predict": 1200, "num_ctx": NUM_CTX},
                   messages=[{"role": "system", "content": SYNTH_SYS},
                             {"role": "user", "content": "[근거]\n" + evidence}])
        draft = _strip_think(r1["message"].get("content", ""))
    except Exception as e:
        return None, f"종합 단계 실패: {e}"
    if not draft:
        return None, "종합 결과가 비어 있음"
    try:
        r2 = _chat(model=model, keep_alive="2m", format="json",
                   options={"temperature": 0, "num_predict": 1200, "num_ctx": NUM_CTX},
                   messages=[{"role": "system", "content": AUDIT_SYS},
                             {"role": "user",
                              "content": "[근거]\n" + evidence + "\n\n[초안 JSON]\n" + draft}])
        audited = _parse(r2["message"].get("content", ""))
    except Exception:
        audited = None       # 감사 실패해도 1차 초안은 살림
    # ★'or' 로 고르면 감사가 다 지운 {} 가 거짓이라 지운 초안이 되살아난다(아스트라 검토)
    return (audited if audited is not None else _parse(draft)), None


# ── 모델 글 거르기: 근거에 없는 숫자 · 화면을 망가뜨릴 길이 ────────────
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?%?")


def _grounded(s, ev_flat):
    """글 속 시세처럼 생긴 숫자(소수점·쉼표·%·세 자리 이상)가 근거에 그대로 있나.
       '3대 지수'·'10년물' 같은 작은 정수는 통과. 없는 숫자가 하나라도 있으면 False → 그 줄은 버린다.
       앞뒤가 숫자로 이어지지 않게 찾는다 — 그냥 포함으로 보면 '4%' 가 '0.45%' 안에서 찾아진다."""
    for tok in _NUM.findall(s):
        pct = tok.endswith("%")
        core = tok.rstrip("%").replace(",", "")
        if not ("." in core or "," in tok or pct or len(core) >= 3):
            continue
        pat = r"(?<![\d.])" + re.escape(core) + (r"\s*%" if pct else r"(?![\d])")
        if not re.search(pat, ev_flat):
            return False
    return True


def _tidy(s, hard, marks=False):
    """모델 글 한 칸 → 깨끗한 한 줄, 또는 ''(버림). 글자가 아니면(객체·숫자·참거짓) 버리고,
       태그·마크다운 기호를 지우고, hard 를 넘으면 버린다 — 중간을 자르면 말이 끊긴 채 나간다.
       marks=True 면 '**중요한 말**' 표시는 남긴다(짝이 안 맞으면 다 지움) — 화면에서 굵게가 된다."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"[`#]+", "", s)
    parts = s.split("**")
    if marks and len(parts) % 2 == 1:                  # '**' 가 짝수 개 = 짝이 맞음
        s = "**".join(p.replace("*", "") for p in parts)
    else:
        s = s.replace("*", "")
    s = re.sub(r"\s+", " ", s).strip(" -·•")
    return s if len(s.replace("**", "")) <= hard else ""


def _strong(s):
    """이스케이프한 뒤 '**말**' → <b>말</b>. 모델이 이유마다 하나씩 표시한 '중요한 말'."""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _esc(s))


def _match_mover(key, movers):
    k = str(key or "").replace(" ", "").lower()
    for m in movers:
        if m["name"].replace(" ", "").lower() in k or m["sym"].lower() == k:
            return m
    return None


def _mentioned(m, news_text):
    """이번 장 기사(제목·요약·본문)에 그 종목이 나오나 — 한글 이름·티커·영문 이름 중 하나라도.
       안 나오는데 붙은 이유는 '반도체 강세에 동반 상승으로 보임' 같은 추측이라 버린다."""
    names = [m["name"], m["sym"], MOVER_EN.get(m["sym"], "")]
    return any(n and re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])", news_text, re.I)
               for n in names)


def _clean(d, data, evidence):
    """모델 JSON → 화면에 넣을 칸 {summary, points, movers{심볼: 이유}, mood}."""
    flat = evidence.replace(",", "")
    out = {"summary": "", "points": [], "movers": {}, "mood": ""}
    if not isinstance(d, dict):
        return out
    s = _tidy(d.get("summary"), HARD["summary"])
    if s and _grounded(s, flat):
        out["summary"] = s
    pts = d.get("points") or []
    for p in ([pts] if isinstance(pts, str) else pts if isinstance(pts, list) else []):
        p = _tidy(p, HARD["point"], marks=True)
        if p and _grounded(p, flat) and p not in out["points"]:
            out["points"].append(p)
    out["points"] = out["points"][:3]
    mv = d.get("movers")
    news_text = "\n".join(f"{it.get('title', '')} {it.get('summary', '')} {it.get('body', '')}"
                          for it in data["news"])
    for k, v in (mv.items() if isinstance(mv, dict) else []):
        m = _match_mover(k, data["movers"])
        v = _tidy(v, HARD["mover"])
        if m and v and _grounded(v, flat) and _mentioned(m, news_text):
            out["movers"][m["sym"]] = v
    mood = _tidy(d.get("mood"), HARD["mood"])
    if mood and _grounded(mood, flat):
        out["mood"] = mood
    if not data["news"]:
        # 이번 장 기사가 0건이면 사건·인과 칸은 코드가 비운다 — 프롬프트로 '비워라' 해도
        # 숫자 없는 지어낸 이유는 거르기에 안 걸린다(아스트라 검토). 심리(mood)는 CNN 숫자가 근거라 둔다.
        out["summary"], out["points"], out["movers"] = "", [], {}
    return out


def _code_summary(data):
    """모델 한 줄이 없을 때 쓰는 기본 한 줄 — 숫자 방향만으로 만든다(지어낼 게 없다)."""
    rows = [r for r in data["indices"] if r["sym"] != "^SOX"] or data["indices"]
    if not rows:
        return "지수 수집 실패 — 종목만 정리"
    # 3대 지수가 다 있을 때만 '주요 지수' — 일부만 잡혔는데 '모두 상승'이라 하면 틀린 말이 된다
    who = "주요 지수" if len(rows) == 3 else "·".join(r["name"] for r in rows)
    both = "모두 " if len(rows) > 1 else ""
    if all(r["pct"] > 0 for r in rows):
        head = f"{who} {both}상승"
    elif all(r["pct"] < 0 for r in rows):
        head = f"{who} {both}하락"
    else:
        head = f"{who} 엇갈림"
    return head          # 52주 최고 종가는 지수 줄에 붙어 있다 — 여기까지 붙이면 폰 한 줄을 넘는다


# ── 조립: 텔레그램 HTML 한 통 ──────────────────────────────────────
def _detail_sections(data, txt, picks, failed=False, w=6):
    """접힌 인용 안에 들어갈 칸들 [(제목, [줄]), ...] — 중요한 순. 넘치면 뒤에서부터 뺀다.
       첫 칸은 제목 없이 — 접힌 상태에서도 첫 몇 줄이 보이므로 거기에 핵심 이유를 둔다."""
    secs = []
    pts = [f"· {_strong(p)}" for p in txt["points"]]
    empty = "· 해석을 못 붙임(모델 답 형식 깨짐) — 숫자만 정리" if failed else \
            "· 이번 장 기사에서 확인된 이유 없음 — 숫자만 정리"
    secs.append(("", pts or [empty]))
    names = {m["sym"]: m for m in data["movers"]}
    why = []
    for sym, v in sorted(txt["movers"].items(), key=lambda kv: -abs(names[kv[0]]["pct"])):
        # 종목 줄은 짧게 두고 이유는 다음 줄로 — 한 줄에 몰면 폰에서 아무 데서나 접힌다
        why += [_row(names[sym]["name"], names[sym]["pct"], w, SECTOR.get(sym, "")), f"└ {_esc(v)}"]
    if why:
        secs.append(("종목별 이유", why))
    fg = data.get("fg") or {}
    if fg.get("ok"):
        mood = [_esc(txt["mood"])] if txt["mood"] else []
        flow = [f"{lab} {fg[k]}" for lab, k in (("1주 전", "prev_week"), ("전날", "prev_close"))
                if fg.get(k) is not None]
        if flow:
            mood.append(" → ".join(flow + [f"오늘 {fg['value']}"]))
        for tag, lab in (("extreme fear", "극단 공포"), ("extreme greed", "극단 탐욕")):
            names_ = [_FG_SHORT.get(c["name"], c["name"]) for c in fg.get("components") or []
                      if c.get("rating") == tag]
            if names_:
                mood.append(f"{lab} — {_esc('·'.join(names_))}")
        if mood:
            secs.append(("심리", mood))
    shown = {m["sym"] for m in picks}
    rest = sorted((m for m in data["movers"] if m["sym"] not in shown), key=lambda m: -m["pct"])
    if rest:
        secs.append(("그 외 종목", [_row(m["name"], m["pct"], w, SECTOR.get(m["sym"], ""))
                                   for m in rest]))                     # 한 줄에 하나
    links = []
    for it in data["news"][:4]:
        href = (it.get("link") or "").replace("&", "&amp;").replace('"', "&quot;")
        src = _cut(it.get("source") or "기사", 14)
        # '· 매체 제목' 이 폰 한 줄에 들어가게 — 앞의 '· '·띄어쓰기 몫 3칸을 빼고 남는 폭만큼 제목
        title = _cut(_clean_title(it.get("title")), LINE_UNITS - 3 - sum(
            2 if ord(c) >= 0x1100 else 1 for c in src))
        if href and title:
            links.append(f'· <a href="{href}">{_esc(src)}</a> {_esc(title)}')
    if links:
        secs.append(("기사", links))
    return secs


def render(data, txt, brain, note=""):
    """첫 화면 = 제목·한 줄 판단·지수·큰 종목·심리(숫자는 전부 코드가 박음),
       자세한 해석은 접힌 인용 안. 모델 글은 전부 이스케이프해서 넣는다 — 모델이 '<' 하나만
       써도 텔레그램이 메시지를 통째로 거절하고, 그러면 브리핑이 하루 종일 재시도만 돈다."""
    s = data.get("session")
    # 숫자 열 폭 = 이 메시지의 모든 등락 중 제일 긴 것 — 지수·종목·접힌 칸까지 한 폭으로 맞춘다
    w = max([len(_pct(x["pct"])) for x in data["indices"] + data["movers"]] or [6])
    head = [f"<b>미국 증시 · {_when(s) if s else '최근 장'}</b>",
            f"<b>{_esc(txt.get('summary') or _code_summary(data))}</b>", ""]   # 한 줄 판단 = 제일 중요
    for r in data["indices"]:
        head.append(_row(r["name"], r["pct"], w, "52주 최고 종가" if r["high"] else "",
                         strong_tag=True))
    picks = _pick_movers(data["movers"])
    if picks:
        head += ["", "<b>크게 움직인 종목</b>"]
        head += [_row(m["name"], m["pct"], w, SECTOR.get(m["sym"], ""), strong=True) for m in picks]
    fg = data.get("fg") or {}
    if fg.get("ok"):
        line = (f"공포·탐욕 <b>{fg['value']} "
                f"{_FG_KO.get(fg.get('class'), _esc(fg.get('class')))}</b>")
        if fg.get("prev_close") is not None:
            line += f" · 전날 {fg['prev_close']}"
        head += ["", line]
    # 꼬리말도 폰 한 줄 — 기사 수는 접힌 '기사' 칸에 있으니 뺐다. 이상이 있을 때만 둘째 줄
    foot = f"Yahoo·CNN 실측 · 해석 {_esc(brain)}" + (f"\n{_esc(note)}" if note else "")
    secs = _detail_sections(data, txt, picks, failed=bool(note), w=w)
    while True:
        body = "\n\n".join((f"<b>{t}</b>\n" if t else "") + "\n".join(ls) for t, ls in secs)
        text = "\n".join(head) + "\n"
        if body:
            text += f"<blockquote expandable>{body}</blockquote>\n"
        text += foot
        if len(text) <= TG_MAX or not secs:
            return text
        secs.pop()                       # 넘치면 덜 중요한 칸부터 뺀다(기사 → 그 외 종목 → …)


def generate(model=BRIEF_MODEL, verbose=False, via="local", log=None):
    """수집 → 해석 칸(JSON, 로컬 2패스 또는 클라우드 1패스) → 코드가 조립한 최종 브리핑.

    via="cloud" 면 로컬 26b 대신 구독 CLI(클로드→아스트라)로 1패스 생성한다.
    GPU 가 게임 등으로 바쁠 때 브리핑을 미루지 않고 그냥 내보내려는 우회로.
    로컬이 실패해도(모델이 안 뜸 등) 그 자리에서 클라우드로 넘어간다 — 예전엔 실패만 남기고
    10분 뒤 다시 로컬을 시도해서 브리핑이 몇 시간씩 늦었다(호윤 9/18: 로컬로만 안 돌아도 됨).
    모델 답이 JSON 이 아니면(형식 깨짐) 해석 칸 없이 숫자만이라도 보낸다.
    """
    data = gather()
    if not data["indices"] and not data["movers"]:
        return {"ok": False, "msg": "시세 수집 실패 — 브리핑 보류(잠시 후 재시도)"}
    today = datetime.now().strftime("%Y-%m-%d")
    evidence = _evidence(data, today)
    if verbose:
        print(evidence, "\n", "=" * 40)

    d, brain = None, ""
    if via != "cloud":
        d, err = _local_brief(model, evidence)
        brain = "" if err else "로컬"
        if err and log:
            log(f"[brief] 로컬 실패({str(err)[:120]}) — 클로드·아스트라로 넘어감")
    if not brain:
        d, brain = _cloud_brief(evidence, log)
    if not brain and via == "cloud":          # 클라우드 먼저였는데 둘 다 막혔으면(한도 등) 로컬이 예비
        if log:
            log("[brief] 클로드·아스트라 둘 다 실패 — 로컬 26b 로")
        d, err = _local_brief(model, evidence)
        brain = "" if err else "로컬"
    if not brain:
        return {"ok": False, "msg": "두뇌(클로드·아스트라·로컬) 모두 실패 — 다음 점검 재시도"}
    txt = _clean(d, data, evidence)
    note = "" if d is not None else "해석 형식 깨짐 — 숫자만"
    s = data.get("session")
    return {"ok": True, "text": render(data, txt, brain, note), "txt": txt,
            "news": data["news"], "web": data["web"], "indices": data["indices"],
            "movers": data["movers"], "brain": brain,
            "session": str(s["date"]) if s and s["closed"] else ""}   # 다 끝난 장만 '보냄'으로 잠근다


def latest_session():
    """지금 기준 가장 최근 정규장 — S&P500 마지막 체결 시각 한 번만 본다(브리핑 전에 '이미 보낸
       장인가' 확인용, 2초 안팎). 못 구하면 None."""
    r = prices._yahoo("^GSPC", "지수")
    return _session(r.get("time")) if r.get("ok") else None


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    # python market_brief.py [--cloud] — 1회 생성·출력(전송 안 함). --cloud = 클로드→아스트라
    r = generate(verbose=True, via="cloud" if "--cloud" in sys.argv else "local")
    print(r.get("text") if r.get("ok") else "실패: " + r.get("msg", "?"))
