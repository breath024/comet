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
from datetime import datetime, timezone

import ollama
import prices
import web
import news

BRIEF_MODEL = "gemma4:26b"            # 종합·해석은 신중하게(정확>속도)
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

# 사건 소싱 = 구글뉴스 RSS, 이번 정규장 개장 뒤 기사만(_fresh_news)
NEWS_QUERIES = [("뉴욕증시", "ko"), ("뉴욕증시 마감", "ko"),
                ("stock market today", "en"), ("chip stocks", "en")]
FRESH_DAYS = 2                                # 세션 시각을 못 구했을 때만 쓰는 예비 기준
DEEP_K = 4                                    # 상위 헤드라인 N개는 본문까지 긁어 해석 깊이 확보
WEB_FALLBACK = ("뉴욕증시 마감 특징주", "kr-ko")   # 뉴스가 빈약할 때만 보조

BIG_MOVE = 1.5        # 첫 화면 '크게 움직인 종목' 기준(%). 넘는 게 3개 미만이면 큰 순서로 3개
MAX_MOVERS = 5
TG_MAX = 3900         # notify.send 가 4000자에서 자른다 — 태그 중간에서 잘리지 않게 여기서 먼저 맞춘다
# 모델 칸 글자 수 — 프롬프트에 주는 목표. 화면을 망가뜨릴 만큼(HARD) 넘치면 그 칸은 버린다.
HARD = {"summary": 48, "point": 90, "mover": 80, "mood": 120}

_WEEK = "월화수목금토일"
_FG_KO = {"extreme fear": "극단 공포", "fear": "공포", "neutral": "중립",
          "greed": "탐욕", "extreme greed": "극단 탐욕"}


def _strip_think(t):
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    t = re.sub(r"^.*?</think>", "", t, flags=re.S)
    return t.strip()


def _esc(s):
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _arrow(p):
    """등락률 → '▲0.58%' / '▼3.18%'. 텔레그램은 글자색을 못 쓰니 모양으로 방향을 보인다."""
    if p is None:
        return "—"
    if p > 0:
        return f"▲{p:.2f}%"
    if p < 0:
        return f"▼{-p:.2f}%"
    return "0.00%"


# ── 세션: 지금 다루는 게 어느 날 정규장인가 ─────────────────────────
def _session(meta):
    """Yahoo meta → 가장 최근 정규장 {date, open_ts, closed}. 못 구하면 None.
       마지막 체결 시각(regularMarketTime)의 현지 날짜가 그 세션의 날이다 — 주말 아침엔 금요일."""
    t, off = meta.get("regularMarketTime"), meta.get("gmtoffset")
    if not t or off is None:
        return None
    d = datetime.fromtimestamp(t + off, tz=timezone.utc).date()
    open_ts = int(datetime(d.year, d.month, d.day, 9, 30, tzinfo=timezone.utc).timestamp()) - off
    return {"date": d, "open_ts": open_ts, "closed": time.time() >= open_ts + 390 * 60}


def _when(s):
    d = s["date"]
    return f"{d.month}/{d.day}({_WEEK[d.weekday()]}) " + ("마감" if s["closed"] else "장중")


def _year_high(sym, price):
    """→ (오늘 종가가 52주 최고 종가인가, meta). 1년 일봉 종가 중 오늘 봉을 뺀 최댓값과 비교."""
    j = prices._get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
                         "?interval=1d&range=1y")
    res = j["chart"]["result"][0]
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quote.get("close") or []) if c]
    if closes and abs(closes[-1] - price) <= price * 0.0005:
        closes = closes[:-1]                 # 마지막 봉 = 오늘 종가 → 빼고 비교
    return bool(closes) and price >= max(closes), res.get("meta") or {}


# ── 사건: 이번 정규장 개장 뒤 구글뉴스 기사만 ─────────────────────
def _fresh_news(since=None):
    """since(이번 세션 개장 시각) 뒤 기사만. 못 구했으면 최근 FRESH_DAYS 일."""
    cutoff = since or (time.time() - FRESH_DAYS * 86400)
    items, seen = [], set()
    for kw, lang in NEWS_QUERIES:
        try:
            for it in news.fetch_keyword(kw, lang=lang, limit=12):
                if it.get("_ts", 0) < cutoff:          # 지난 세션·오래된 기사 = 제외
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
                if s.get("readable") and len(s.get("text") or "") > len(best):
                    best = s["text"]
            if best:
                it["body"] = best[:900]
                picked += 1
        except Exception:
            pass
    return news_items


# ── 수집: 그라운딩 숫자(Yahoo·CNN) + 이번 세션 사건(news, 폴백 web) ─────
def gather():
    """→ {session, indices, movers, news, web, fg}. 숫자는 전부 실측, LLM 안 거침."""
    indices, movers, session = [], [], None
    for sym, name in INDICES:
        r = prices._yahoo(sym, "지수")
        if not (r.get("ok") and r.get("change_pct") is not None):
            continue
        row = {"sym": sym, "name": name, "price": r["price"], "pct": r["change_pct"],
               "high": False}
        try:
            row["high"], meta = _year_high(sym, r["price"])
            if session is None:
                session = _session(meta)
        except Exception:
            pass
        indices.append(row)
    for sym, name in MOVERS:
        r = prices._yahoo(sym, "주가")
        if r.get("ok") and r.get("change_pct") is not None:
            movers.append({"sym": sym, "name": name, "pct": r["change_pct"]})
    news_items = _deepen(_fresh_news(session["open_ts"] if session else None))
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
    """첫 화면에 올릴 종목 — BIG_MOVE% 넘게 움직인 것(최대 MAX_MOVERS), 모자라면 큰 순서로 3개."""
    by = sorted(movers, key=lambda m: -abs(m["pct"]))
    big = [m for m in by if abs(m["pct"]) >= BIG_MOVE][:MAX_MOVERS]
    return big if len(big) >= 3 else by[:3]


def _evidence(data, today):
    s = data.get("session")
    ev = [f"[오늘(한국): {today} · 다루는 장: 미국 정규장 "
          f"{_when(s) if s else '(날짜 미확인)'}. 이 세션 얘기만 써라]",
          "",
          "[지수 — Yahoo 실측]",
          " · ".join(f"{r['name']} {r['pct']:+.2f}%" + (" (52주 최고 종가)" if r["high"] else "")
                     for r in data["indices"]) or "(지수 수집 실패)",
          "",
          "[종목 — Yahoo 실측]",
          " · ".join(f"{m['name']}({m['sym']}) {m['pct']:+.2f}%" for m in data["movers"])
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
    "[근거]만 보고 아래 JSON 하나만 출력해라. 앞뒤 설명·마크다운·코드펜스 금지.\n"
    '{"summary": "...", "points": ["...", "..."], "movers": {"종목이름": "..."}, "mood": "..."}\n'
    "- summary: 오늘 장 성격 한 줄, 34자 이내. 예) 실적 기대에 기술주 주도 상승, 반도체는 엇갈림\n"
    "- points: 오늘 장을 움직인 이유·흐름 최대 3개, 각 50자 이내, 한 줄에 한 가지. 이번 세션 기사에 있는 것만.\n"
    "- movers: [크게 움직인 종목] 중 이번 세션 기사에 움직인 이유가 나온 종목만 "
    '{"종목이름": "이유 45자 이내"}. 이유가 기사에 없으면 넣지 마라. 하나도 없으면 {}.\n'
    "- mood: 공포·탐욕 세부지표가 '뜻하는' 분위기 한 줄, 50자 이내. 어느 지표가 극단인지 나열은 코드가 "
    "따로 붙이니 반복하지 말고 해석만(예: '겉은 중립, 속은 소수 대형주 쏠림 — 상승 폭 좁음 경계'). "
    "극단 공포=과매도·반등 기대, 극단 탐욕=과열 경계 맥락. 단정하지 말고 분위기로.\n"
    "[철칙]\n"
    "- 등락률·지수값·가격·% 같은 시세 숫자를 쓰지 마라. 숫자는 코드가 옆에 붙인다.\n"
    "- 이번 세션 기사에 없는 사건·인과는 쓰지 마라. 전망·추천·종목 비교 글('어느 주식이 나을까', "
    "'목표가', '사야 할 종목')은 오늘 등락의 이유가 아니다.\n"
    "- 사실은 기사대로, 네 추론은 '~로 보임'으로 끝내라. 과장 형용사·감탄 금지, 담백하게.\n"
    "- 말투는 기사 제목 같은 짧은 개조식(예: '금리 하락에 기술주 강세'). '~습니다'·'~했어' 어미 금지.\n"
    "- 근거가 빈약하면 points·movers 를 줄이거나 비워라. 지어내지 마라.\n"
    "- HTML·이모지·줄바꿈 금지.")

AUDIT_SYS = (
    "너는 냉정한 사실 감사관이다. [초안 JSON]을 [근거]와 대조해 고친 JSON 하나만 낸다(같은 키, 설명 금지).\n"
    "- 근거의 '이번 세션 기사'에 없는 사건·인과는 지운다. 전망·추천 글을 이유로 쓴 줄도 지운다.\n"
    "- 단정한 인과('때문에')는 '~영향으로 보임'으로 낮춘다.\n"
    "- 등락률·지수값 같은 시세 숫자는 지운다(코드가 따로 붙인다).\n"
    "- 글자 수(summary 34 · points 각 50 · movers 각 45 · mood 50)를 넘으면 줄인다.")


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
                   options={"temperature": 0.2, "num_predict": 1200},
                   messages=[{"role": "system", "content": SYNTH_SYS},
                             {"role": "user", "content": "[근거]\n" + evidence}])
        draft = _strip_think(r1["message"].get("content", ""))
    except Exception as e:
        return None, f"종합 단계 실패: {e}"
    if not draft:
        return None, "종합 결과가 비어 있음"
    try:
        r2 = _chat(model=model, keep_alive="2m", format="json",
                   options={"temperature": 0, "num_predict": 1200},
                   messages=[{"role": "system", "content": AUDIT_SYS},
                             {"role": "user",
                              "content": "[근거]\n" + evidence + "\n\n[초안 JSON]\n" + draft}])
        audited = _parse(r2["message"].get("content", ""))
    except Exception:
        audited = None       # 감사 실패해도 1차 초안은 살림
    return audited or _parse(draft), None


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


def _tidy(s, hard):
    """모델 글 한 칸 → 깨끗한 한 줄, 또는 ''(버림). 태그·마크다운 기호를 지우고, hard 를 넘으면
       버린다 — 중간을 자르면 말이 끊긴 채 나가니 차라리 비운다."""
    s = re.sub(r"<[^>]*>", "", str(s or ""))
    s = re.sub(r"[*`#]+", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -·•")
    return s if len(s) <= hard else ""


def _match_mover(key, movers):
    k = str(key or "").replace(" ", "").lower()
    for m in movers:
        if m["name"].replace(" ", "").lower() in k or m["sym"].lower() == k:
            return m
    return None


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
        p = _tidy(p, HARD["point"])
        if p and _grounded(p, flat) and p not in out["points"]:
            out["points"].append(p)
    out["points"] = out["points"][:3]
    mv = d.get("movers")
    for k, v in (mv.items() if isinstance(mv, dict) else []):
        m = _match_mover(k, data["movers"])
        v = _tidy(v, HARD["mover"])
        if m and v and _grounded(v, flat):
            out["movers"][m["sym"]] = v
    mood = _tidy(d.get("mood"), HARD["mood"])
    if mood and _grounded(mood, flat):
        out["mood"] = mood
    return out


def _code_summary(data):
    """모델 한 줄이 없을 때 쓰는 기본 한 줄 — 숫자 방향만으로 만든다(지어낼 게 없다)."""
    main = [r for r in data["indices"] if r["sym"] != "^SOX"]
    if not main:
        return "지수 수집 실패 — 종목만 정리"
    if all(r["pct"] > 0 for r in main):
        head = "주요 지수 모두 상승"
    elif all(r["pct"] < 0 for r in main):
        head = "주요 지수 모두 하락"
    else:
        head = "주요 지수 엇갈림"
    highs = [r["name"] for r in data["indices"] if r["high"]]
    return head + (f" · {'·'.join(highs)} 52주 신고가" if highs else "")


# ── 조립: 텔레그램 HTML 한 통 ──────────────────────────────────────
def _detail_sections(data, txt, picks, failed=False):
    """접힌 인용 안에 들어갈 칸들 [(제목, [줄]), ...] — 중요한 순. 넘치면 뒤에서부터 뺀다.
       첫 칸은 제목 없이 — 접힌 상태에서도 첫 몇 줄이 보이므로 거기에 핵심 이유를 둔다."""
    secs = []
    pts = [f"· {_esc(p)}" for p in txt["points"]]
    empty = "· 해석을 못 붙임(모델 답 형식 깨짐) — 숫자만 정리" if failed else \
            "· 이번 장 기사에서 확인된 이유 없음 — 숫자만 정리"
    secs.append(("", pts or [empty]))
    names = {m["sym"]: m for m in data["movers"]}
    why = [f"{_esc(names[sym]['name'])} {_arrow(names[sym]['pct'])} — {_esc(v)}"
           for sym, v in sorted(txt["movers"].items(), key=lambda kv: -abs(names[kv[0]]["pct"]))]
    if why:
        secs.append(("종목별 이유", why))
    fg = data.get("fg") or {}
    if fg.get("ok"):
        mood = [_esc(txt["mood"])] if txt["mood"] else []
        flow = [f"{lab} {fg[k]}" for lab, k in (("1주 전", "prev_week"), ("전날", "prev_close"))
                if fg.get(k) is not None]
        if flow:
            mood.append("흐름: " + " → ".join(flow + [f"오늘 {fg['value']}"]))
        for tag, lab in (("extreme fear", "극단 공포"), ("extreme greed", "극단 탐욕")):
            names_ = [c["name"] for c in fg.get("components") or [] if c.get("rating") == tag]
            if names_:
                mood.append(f"{lab}: {_esc('·'.join(names_))}")
        if mood:
            secs.append(("심리", mood))
    shown = {m["sym"] for m in picks}
    rest = sorted((m for m in data["movers"] if m["sym"] not in shown), key=lambda m: -m["pct"])
    if rest:
        secs.append(("그 외 종목", [" · ".join(f"{_esc(m['name'])} {_arrow(m['pct'])}"
                                              for m in rest)]))
    links = []
    for it in data["news"][:4]:
        href = (it.get("link") or "").replace("&", "&amp;").replace('"', "&quot;")
        title = _clean_title(it.get("title"))
        if len(title) > 40:
            title = title[:39].rstrip() + "…"
        if href and title:
            links.append(f'<a href="{href}">{_esc(it.get("source") or "기사")}: {_esc(title)}</a>')
    if links:
        secs.append(("기사", links))
    return secs


def render(data, txt, brain, note=""):
    """첫 화면 = 제목·한 줄 판단·지수·큰 종목·심리(숫자는 전부 코드가 박음),
       자세한 해석은 접힌 인용 안. 모델 글은 전부 이스케이프해서 넣는다 — 모델이 '<' 하나만
       써도 텔레그램이 메시지를 통째로 거절하고, 그러면 브리핑이 하루 종일 재시도만 돈다."""
    s = data.get("session")
    head = [f"<b>미국 증시 · {_when(s) if s else '최근 장'}</b>",
            _esc(txt.get("summary") or _code_summary(data)), ""]
    for r in data["indices"]:
        head.append(f"{_esc(r['name'])}  <b>{_arrow(r['pct'])}</b>"
                    + ("  <i>52주 신고가</i>" if r["high"] else ""))
    picks = _pick_movers(data["movers"])
    if picks:
        head += ["", "<b>크게 움직인 종목</b>"]
        head += [f"{_esc(m['name'])}  <b>{_arrow(m['pct'])}</b>" for m in picks]
    fg = data.get("fg") or {}
    if fg.get("ok"):
        line = (f"공포·탐욕 <b>{fg['value']} "
                f"{_FG_KO.get(fg.get('class'), _esc(fg.get('class')))}</b>")
        if fg.get("prev_close") is not None:
            line += f" · 전날 {fg['prev_close']}"
        head += ["", line]
    foot = (f"<i>숫자 Yahoo·CNN 실측 · 해석 {_esc(brain)} · 이번 장 기사 {len(data['news'])}건"
            + (f" · {_esc(note)}" if note else "") + "</i>")
    secs = _detail_sections(data, txt, picks, failed=bool(note))
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
    모델 답이 JSON 이 아니면(형식 깨짐) 해석 칸 없이 숫자만이라도 보낸다.
    """
    data = gather()
    if not data["indices"] and not data["movers"]:
        return {"ok": False, "msg": "시세 수집 실패 — 브리핑 보류(잠시 후 재시도)"}
    today = datetime.now().strftime("%Y-%m-%d")
    evidence = _evidence(data, today)
    if verbose:
        print(evidence, "\n", "=" * 40)

    if via == "cloud":
        d, brain = _cloud_brief(evidence, log)
        if not brain:
            return {"ok": False, "msg": "클라우드 두뇌(클로드·아스트라) 둘 다 실패 — 다음 점검 재시도"}
    else:
        d, err = _local_brief(model, evidence)
        if err:
            return {"ok": False, "msg": err}
        brain = "로컬"
    txt = _clean(d, data, evidence)
    note = "" if d is not None else "해석 형식 깨짐 — 숫자만"
    return {"ok": True, "text": render(data, txt, brain, note), "txt": txt,
            "news": data["news"], "web": data["web"], "indices": data["indices"],
            "movers": data["movers"], "brain": brain}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    # python market_brief.py [--cloud] — 1회 생성·출력(전송 안 함). --cloud = 클로드→아스트라
    r = generate(verbose=True, via="cloud" if "--cloud" in sys.argv else "local")
    print(r.get("text") if r.get("ok") else "실패: " + r.get("msg", "?"))
