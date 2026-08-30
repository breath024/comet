# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 종목·테마 분석 브레인 (analyst)
#   목적: "증권사 AI 컨센서스를 알고, 거기에 역심리·2차 파급효과를 겹쳐
#         이중 판단으로 할루시네이션까지 잡는" 종목/테마 분석.
#   설계: Only Money 9단 파이프라인(레짐→촉매→군중심리→레드팀→최종콜)의 사상을
#         로컬용으로 압축. 수집은 LLM 안 거치고(web.py·Yahoo 실데이터), 판단만 2패스.
#   재활용: web.py(검색·본문·출처등급), prices.py(_get_json). 의존성 추가 0.
#
#   파이프라인:
#     0) 수집  : 티커 감지(미국+한국 .KS/.KQ) → 1년 일봉 실계산(52주·수익률·이평선·RSI/ATR 셋업) + 웹 2갈래(본주제 / 2차파급)
#     1) 1차판단: '컨센서스 분석가' = 시장/증권사 컨센서스 + 2차 파급효과 지도 + 잠정 방향
#     2) 2차판단: '역발상 검증관' = 컨센서스 약점·역발상 + 1차의 미근거 수치 적발(할루시네이션)
#                 → 확인/추론/미확인 분리한 최종 콜. (= 이중 판단)
# ═══════════════════════════════════════════════════════════════
import os
import re
import json
import statistics

import ollama
import web
import prices
import financials
import marketlog
import finance_news          # Only Money 엔진과 동일한 금융 RSS(1차 근거)
import trace as _trace

# 분석 전용 모델 — 정확·날카로움이 1순위(속도 무관)라 양 패스 모두 최고 성능 모델 고정.
# (2026-06-19 A/B/C 실측: 27b/27b가 1차 추론 깊이·2차 할루시네이션 감사 모두 최고. 하이브리드는 14b 감사관이 약한 고리가 됨.)
ANALYST_MODEL = "gemma4:26b"
PLANNER_MODEL = "gemma4:26b"   # 나라·언어 선정은 가벼운 작업 → 빠른 모델

# 언어 → DuckDuckGo 지역·언어 코드(현지 결과를 받기 위함) / 표시용 국가명
LANG2KL = {"ko": "kr-ko", "en": "us-en", "zh": "cn-zh", "tw": "tw-tzh",
           "ja": "jp-jp", "de": "de-de", "fr": "fr-fr"}
LANG_NAME = {"ko": "한국", "en": "미국/영어권", "zh": "중국", "tw": "대만",
             "ja": "일본", "de": "독일", "fr": "프랑스"}

PLAN_PROMPT = (
    "이 종목/테마의 공급망·핵심시장·경쟁국을 고려해, 정보를 모을 나라 2~3곳을 고르고 "
    "그 나라 언어로 검색할 핵심 쿼리를 만들어라. 한국(ko)은 기본 포함. JSON만 출력.\n"
    "형식: {\"targets\":[{\"lang\":\"en\",\"query\":\"현지어 검색어\"}, ...]}\n"
    "lang은 ko/en/zh/tw/ja/de/fr 중. query는 그 언어로 자연스럽게(영어권=영어, 대만=번체중국어, 일본=일본어). "
    "예(엔비디아): {\"targets\":[{\"lang\":\"en\",\"query\":\"Nvidia NVDA stock outlook 2026\"},"
    "{\"lang\":\"zh\",\"query\":\"輝達 NVDA 股價 展望 2026\"},{\"lang\":\"ko\",\"query\":\"엔비디아 HBM 수혜 전망\"}]}\n"
    "대상: ")


# ── 안정성: 얼어붙은 ollama 대비 타임아웃(27b 2패스라 정상 ~1.5분 → 600s 넉넉).
#    출력 상한(num_predict)은 아래 각 호출에 이미 박힘(1300/1800) → 폭주는 이미 차단됨.
_AC = ollama.Client(timeout=600)


def _plan_languages(query, model=PLANNER_MODEL):
    """종목/테마와 엮인 나라들을 골라 현지어 검색어를 짠다. 실패 시 한국어+영어 폴백."""
    try:
        r = _AC.chat(model=model, format="json",
                     options={"temperature": 0, "num_predict": 512},
                     messages=[{"role": "user", "content": PLAN_PROMPT + query}])
        targets = (json.loads(r["message"]["content"]) or {}).get("targets") or []
        clean, langs = [], set()
        for t in targets:
            lg, q = t.get("lang"), (t.get("query") or "").strip()
            if lg in LANG2KL and q and lg not in langs:
                clean.append({"lang": lg, "query": q})
                langs.add(lg)
            if len(clean) >= 3:
                break
        if "ko" not in langs:
            clean.append({"lang": "ko", "query": query + " 전망"})
        if clean:
            return clean
    except Exception:
        pass
    return [{"lang": "ko", "query": query + " 전망 분석"},
            {"lang": "en", "query": query + " stock outlook 2026"}]


# 종목 이름→티커 단일 출처: 미국=us_stocks, 한국=kr_stocks (분석·시세 공용)
from us_stocks import NAME2TICKER
from kr_stocks import NAME2TICKER_KR

# 분석 요청 vs 단순 시세 조회 구분용 (comet.py 가 참고)
ANALYZE_KW = ("분석", "전망", "투자의견", "사야", "팔아", "사도", "살까", "팔까",
              "들어가", "물렸", "어떻게 봐", "어떡해", "역발상", "역심리", "컨센서스",
              "수혜주", "관련주", "테마", "업황", "비중", "포트", "리밸런", "갈아타")


_HOLDINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "holdings.txt")


def _load_holdings():
    """vision.read_holdings가 저장한 보유 내역(스샷에서 읽은 표). 없으면 None."""
    try:
        with open(_HOLDINGS_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            return t or None
    except Exception:
        return None


def _detect_ticker(q):
    ql = (q or "").lower()
    # 한국 종목 이름 먼저(값에 .KS/.KQ 접미사 포함) — "SK하이닉스"가 대문자규칙에 'SK'로
    #   새지 않게 이름 매칭을 우선한다.
    for name, tk in NAME2TICKER_KR.items():
        if name in ql:
            return tk
    for name, tk in NAME2TICKER.items():
        if name in ql:
            return tk
    # 한국 6자리 종목코드 — 접미사 없으면 _history 가 .KS→.KQ 순으로 시도
    m = re.search(r"\b(\d{6})\b", q or "")
    if m:
        return m.group(1)
    # 대문자 티커 토큰(2~5글자)
    m = re.search(r"\b([A-Z]{2,5})\b", q or "")
    return m.group(1) if m else None


def _rsi(closes, n=14):
    """Wilder RSI(14). 과매수(≥70)/과매도(≤30) 판정용. 데이터 부족하면 None."""
    if len(closes) < n + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(deltas)):     # Wilder 평활
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


def _atr(highs, lows, closes, n=14):
    """Wilder ATR(14). 손절폭·변동성 근거용(절대값, 통화 단위). 부족하면 None."""
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i - 1]),
                 abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    atr = sum(trs[:n]) / n
    for i in range(n, len(trs)):
        atr = (atr * (n - 1) + trs[i]) / n
    return atr


def _history(ticker):
    """Yahoo 1년 일봉 → 현재가·52주위치·수익률·이평선 + RSI/ATR 셋업지표를 실계산.
       숫자는 전부 파이썬 계산(LLM 안 거침) = 날조 불가. 실패하면 None.
       접미사 없는 한국 6자리 코드는 .KS(코스피)→.KQ(코스닥) 순으로 시도한다."""
    cands = [ticker]
    if re.fullmatch(r"\d{6}", ticker or ""):
        cands = [ticker + ".KS", ticker + ".KQ"]
    # 6자리 코드는 .KS/.KQ 양쪽에서 데이터가 나올 수 있는데(한쪽은 거래량 없는 '유령' 펀드 listing)
    #   진짜 상장은 regularMarketVolume 이 채워진다 → 거래량 있는 쪽을 고른다.
    picked = None    # (tk, meta, highs, lows, closes, has_vol)
    for tk in cands:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
               "?interval=1d&range=1y")
        try:
            j = prices._get_json(url)
            res = j["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            # high/low/close 를 같은 행으로 정렬(ATR은 정렬된 삼중조가 필요)
            rows = [(h, l, c) for h, l, c in zip(q.get("high") or [],
                                                 q.get("low") or [],
                                                 q.get("close") or [])
                    if None not in (h, l, c)]
        except Exception:
            continue
        if len(rows) < 30:
            continue
        m = res["meta"]
        has_vol = m.get("regularMarketVolume") is not None
        cand = (tk, m, [r[0] for r in rows], [r[1] for r in rows],
                [r[2] for r in rows], has_vol)
        if picked is None or (has_vol and not picked[5]):
            picked = cand
        if has_vol:          # 거래량 있는 진짜 상장 찾으면 확정
            break
    if picked is None:
        return None
    used, meta, highs, lows, closes, _ = picked
    price = meta.get("regularMarketPrice") or closes[-1]
    # 전일 등락은 '일봉 마지막 2개'로 계산. (range=1y면 meta.chartPreviousClose=1년전 종가라 못 씀)
    prev = closes[-2] if len(closes) >= 2 else None
    hi52, lo52 = max(closes), min(closes)
    pos52 = round((price - lo52) / (hi52 - lo52) * 100, 1) if hi52 > lo52 else None
    sma50 = statistics.mean(closes[-50:]) if len(closes) >= 50 else None
    sma200 = statistics.mean(closes[-200:]) if len(closes) >= 200 else None
    atr = _atr(highs, lows, closes)

    def _ret(n):
        return round((price / closes[-n] - 1) * 100, 1) if len(closes) > n else None

    return {
        "ticker": used, "price": round(price, 2),
        "currency": meta.get("currency", "USD"),
        "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
        "hi52": round(hi52, 2), "lo52": round(lo52, 2), "pos52": pos52,
        "ret_1m": _ret(21), "ret_3m": _ret(63), "ret_6m": _ret(126),
        "vs_sma50": round((price / sma50 - 1) * 100, 1) if sma50 else None,
        "vs_sma200": round((price / sma200 - 1) * 100, 1) if sma200 else None,
        "rsi14": _rsi(closes),
        "atr14": round(atr, 2) if atr else None,
        "atr_pct": round(atr / price * 100, 1) if atr and price else None,
        "sma50": round(sma50, 2) if sma50 else None,
        "source": "Yahoo Finance (1y, 실계산)",
    }


def _regime(px):
    """실계산 수치(52주 위치·이평선·추세)로 '국면·선반영'을 기계적으로 단정.
       모델이 선반영 단계에서 '자료 없음'으로 도망치지 못하게 근거에 직접 박는다."""
    pos = px.get("pos52")
    if pos is None:
        return None
    s50, s200 = px.get("vs_sma50"), px.get("vs_sma200")
    if pos >= 85:
        ploc = "52주 고점권"
    elif pos >= 65:
        ploc = "52주 상단"
    elif pos >= 35:
        ploc = "52주 중간대"
    elif pos >= 15:
        ploc = "52주 하단"
    else:
        ploc = "52주 바닥권"
    if s50 is not None and s200 is not None:
        if s50 > 0 and s200 > 0:
            trend = "상승추세(50·200일선 위)"
        elif s50 < 0 and s200 < 0:
            trend = "하락추세(50·200일선 아래)"
        else:
            trend = "추세 혼조/전환구간"
    else:
        trend = "추세 불명"
    up = (s50 or 0) > 0 and (s200 or 0) > 0
    down = (s50 or 0) < 0 and (s200 or 0) < 0
    high, low = pos >= 75, pos <= 25
    if high and up:
        v = "이미 크게 올라 고점권·상승추세 → 쉬운 돈은 상당부분 지났다(추격 부담, 눌림 대기 유리)"
    elif low and down:
        v = "고점 대비 크게 빠진 바닥권·하락추세 → 낙폭과대 반등 후보일 순 있으나 추세반전 확인 전 진입은 '떨어지는 칼' 위험"
    elif high:
        v = "고점권이나 추세 식는 중 → 차익실현 압력 구간"
    elif low:
        v = "바닥권에서 추세 돌리는 조짐 → 반등 초입 가능성(확인 필요)"
    elif up:
        v = "중간대·상승추세 → 추세 여력 일부 남아있을 수 있음"
    elif down:
        v = "중간대·하락추세 → 반등보다 약세 추종 우위"
    else:
        v = "뚜렷한 쏠림 없는 중립 구간"
    return f"{ploc} · {trend} → {v}"


def _px_lines(px):
    if not px:
        return "(가격 데이터 없음 — 티커 미감지/조회실패. 웹 자료로만 판단)"
    parts = [f"{px['ticker']} 현재가 {px['price']:,} {px['currency']}"]
    if px.get("change_pct") is not None:
        parts.append(f"(전일 {px['change_pct']:+.2f}%)")
    L = [" ".join(parts)]
    if px.get("pos52") is not None:
        L.append(f"- 52주 위치 {px['pos52']}% (저 {px['lo52']:,} ~ 고 {px['hi52']:,})")
    rets = [f"1M {px['ret_1m']:+.1f}%" if px.get("ret_1m") is not None else None,
            f"3M {px['ret_3m']:+.1f}%" if px.get("ret_3m") is not None else None,
            f"6M {px['ret_6m']:+.1f}%" if px.get("ret_6m") is not None else None]
    rets = [r for r in rets if r]
    if rets:
        L.append("- 수익률: " + " · ".join(rets))
    sma = []
    if px.get("vs_sma50") is not None:
        sma.append(f"50일선 {px['vs_sma50']:+.1f}%")
    if px.get("vs_sma200") is not None:
        sma.append(f"200일선 {px['vs_sma200']:+.1f}%")
    if sma:
        L.append("- 이평선 이격: " + " · ".join(sma)
                 + " (양수=위=상승추세, 음수=아래=약세)")
    # 셋업 지표(RSI/ATR) — 진입 타이밍·손절폭의 '계산된' 근거(날조 아님)
    setup = []
    if px.get("rsi14") is not None:
        r = px["rsi14"]
        zone = "과매수" if r >= 70 else "과매도" if r <= 30 else "중립"
        setup.append(f"RSI(14) {r} ({zone})")
    if px.get("atr_pct") is not None:
        setup.append(f"ATR {px['atr_pct']}%/일")
    if setup:
        line = "- 📐 셋업 지표: " + " · ".join(setup)
        if px.get("atr14") and px.get("price"):
            stop = round(px["price"] - 2 * px["atr14"], 2)
            dist = round(2 * px["atr_pct"], 1) if px.get("atr_pct") else None
            line += (f"  ‖ 2×ATR 손절선 ≈ {stop:,}"
                     + (f" (현재가 -{dist}%)" if dist is not None else ""))
        if px.get("sma50"):
            line += f"  ‖ 눌림 참고선(50일선) {px['sma50']:,}"
        L.append(line)
    reg = _regime(px)
    if reg:
        L.append("- 📌 기술적 국면(계산값): " + reg)
    return "\n".join(L)


def _src_block(research, tag):
    """web.research 결과 → 출처등급·날짜 붙인 근거 블록."""
    if not research or not research.get("ok"):
        return f"({tag}: 자료 못 가져옴)"
    out = []
    for s in research.get("sources", []):
        head = (f"[{tag}{s['n']}] (분류 {s['tier']}={s['tier_label']} · {s['domain']}"
                + (f" · {s.get('lang')}자료" if s.get("lang") else "")
                + f" · 날짜 {s.get('date') or '미상'}"
                + (" · 원천인용 있음" if s.get("root_ref") else "") + ")")
        out.append(head + f"\n{s['title']}\n{s.get('text', s.get('snippet', ''))}")
    return "\n\n".join(out)


def _snip_block(search, tag):
    """web.search 스니펫(본문 안 긁음, 빠름) → 2차효과 아이디어용 가벼운 근거."""
    if not search or not search.get("ok"):
        return f"({tag}: 자료 못 가져옴)"
    out = []
    for i, r in enumerate(search.get("results", []), 1):
        out.append(f"[{tag}{i}] {r['title']}\n  {r.get('snippet', '')}\n  ({r['url']})")
    return "\n".join(out)


# ── 0) 수집 ──────────────────────────────────────────────────────
def gather(query, verbose=True, trace=None):
    tr = trace or _trace.Trace(verbose)
    ticker = _detect_ticker(query)
    px = _history(ticker) if ticker else None
    if px:                          # 6자리 코드가 .KS/.KQ로 해석되면 그 티커로 통일
        ticker = px["ticker"]
    # 재무·실적(펀더멘털) — 블로그 풍문 대신 Yahoo 실데이터로 그라운딩
    fin = None
    if ticker:
        fin = financials.fundamentals(ticker)
        if fin.get("ok"):
            tr.step("재무·실적", f"{ticker} · 펀더멘털·밸류에이션·실적추세 (Yahoo 무키)")
    # 1차 근거 = Only Money 엔진과 동일한 금융 RSS(Yahoo 종목별·CNBC·MarketWatch·Investing).
    # 덕덕고는 "엔비디아 사도될까" 같은 질문에 티스토리·블로그(등급3)만 물어와 컨센서스를
    # 부실하게 만들었다 → 금융 매체(등급2)를 1차로 쓰고, 텅 비면 덕덕고 다국어로 폴백.
    plan = []   # (top-level 반환 호환용; 실제 라벨은 main["plan"])
    tr.step("금융 뉴스", "Only Money 엔진 소스(Yahoo 종목·CNBC·MarketWatch·Investing)")
    main = finance_news.collect(query, ticker=ticker, n_max=6)
    if main.get("ok"):
        src_label = (main.get("plan") or ["금융 RSS"])[0]
        tr.step("자료 읽기", f"{main['count']}개 출처 · {src_label} · 전부 보도언론(등급2)")
        for s in main["sources"]:
            tr.item(f'{s["domain"]} · {s.get("date") or "날짜미상"} — {s["title"][:50]}',
                    flag="미국/영어권")
    else:
        # 폴백: 기존 덕덕고 다국어 수집(금융 RSS가 텅 빈 드문 경우)
        tr.step("폴백 검색", "금융 RSS 비어 덕덕고 다국어로 보강")
        plan = _plan_languages(query)
        for t in plan:
            country = LANG_NAME.get(t["lang"], t["lang"])
            tr.item(f'{country} — "{t["query"]}"', flag=country)
        all_src = []
        for t in plan:
            kl = LANG2KL.get(t["lang"], "wt-wt")
            r = web.research(t["query"], n_sources=3, kl=kl, lang=LANG_NAME.get(t["lang"], t["lang"]))
            if r.get("ok"):
                all_src.extend(r["sources"])
        seen, merged = set(), []
        for s in all_src:
            if s["domain"] in seen:
                continue
            seen.add(s["domain"])
            s["n"] = len(merged) + 1
            merged.append(s)
        t1 = sum(1 for s in merged if s["tier"] == 1)
        t2 = sum(1 for s in merged if s["tier"] == 2)
        t3 = sum(1 for s in merged if s["tier"] >= 3)
        tr.step("자료 읽기", f"{len(merged)}개 출처 · 원천 {t1} · 보도 {t2} · 참고 {t3}")
        main = {"ok": bool(merged), "count": len(merged), "sources": merged,
                "best_tier": min([s["tier"] for s in merged], default=3),
                "plan": [LANG_NAME.get(t["lang"], t["lang"]) for t in plan]}
    second = web.search(query + " 수혜주 관련 산업 파급 영향", n=6)
    # 전체 미장 군중심리(CNN Fear&Greed) — 역발상 패스가 '분위기'가 아닌 숫자로 군중과 반대를 보게
    sentiment = prices.fear_greed("")
    if sentiment.get("ok"):
        tr.step("군중심리", f"CNN F&G {sentiment['value']} ({sentiment['class']}) — 역발상 가중")
    return {"query": query, "ticker": ticker, "px": px, "fin": fin,
            "main": main, "second": second, "plan": plan,
            "sentiment": sentiment, "holdings": _load_holdings()}


def _sources_footer(ev):
    """[본N]/[파N] → 제목 + 링크. 나중에 검토할 수 있게 답 끝에 붙인다(출처 추적성)."""
    lines = []
    for s in (ev.get("main") or {}).get("sources", []):
        ctry = f"·{s.get('lang')}" if s.get("lang") else ""
        lines.append(f"- [본{s['n']}] (등급{s['tier']}{ctry}·{s['domain']}) {s['title'][:60]}\n  {s['url']}")
    sec = (ev.get("second") or {}).get("results", [])
    for i, r in enumerate(sec, 1):
        lines.append(f"- [파{i}] {r['title'][:60]}\n  {r['url']}")
    return "\n".join(lines) if lines else "(수집된 출처 없음)"


def _evidence(ev):
    return (
        f"[분석 대상] {ev['query']}"
        + (f"  (감지 티커: {ev['ticker']})" if ev["ticker"] else "") + "\n\n"
        "=== 가격·추세 (실계산, 날조 아님) ===\n" + _px_lines(ev["px"]) + "\n\n"
        + (("=== 펀더멘털·실적 (Yahoo 실데이터, 날조 아님) ===\n" + financials.fmt(ev["fin"]) + "\n\n")
           if ev.get("fin") and ev["fin"].get("ok") else "")
        + "=== 본 주제 자료 (다국어 수집: "
        + ", ".join((ev.get("main") or {}).get("plan", []) or ["한국"]) + ") ===\n"
        + _src_block(ev["main"], "본") + "\n\n"
        "=== 2차 파급·관련 산업 단서 (검색 요약) ===\n" + _snip_block(ev["second"], "파") + "\n"
        + (("\n=== 시장 군중심리 (CNN Fear&Greed · 전체 미장, 역발상 신호) ===\n"
            + prices.fmt(ev["sentiment"]) + "\n")
           if ev.get("sentiment") and ev["sentiment"].get("ok") else "")
        + (("\n=== 호윤 실제 보유 현황 (스샷에서 읽음) ===\n" + ev["holdings"] + "\n")
           if ev.get("holdings") else "")
    )


# ── 1) 1차 판단: 컨센서스 분석가 ─────────────────────────────────
# 시스템은 '역할+철칙'만 짧게. 형식·강제규칙은 유저 메시지 끝(최신 위치)에 둬서 모델이 따르게 한다.
CONSENSUS_SYS = ("너는 자기 돈을 직접 굴리는 냉정한 애널리스트다. 철칙: 아래 [근거]에 실제로 적힌 "
                 "사실·숫자만 쓴다 — 머릿속에서 시총·매출·시장규모·점유율 숫자를 꺼내 쓰면 그 답은 "
                 "실패다. 근거에 있으면 [본N]/[파N]을 달고, 없으면 그 수치는 안 쓴다. '수혜 가능' 같은 "
                 "두루뭉술한 말·양비론·에세이 금지. 종목/회사는 이름으로 콕 집고 방향(↑/↓)을 단정해라. "
                 "한국어, 짧고 날카롭게.")


# ── 2) 2차 판단: 역발상 검증관 (= 이중 판단 + 할루시네이션 차단) ──
CHALLENGER_SYS = ("너는 역발상 검증관 겸 사실 감사관이다. 임무는 앞 [1차 분석]을 의심하고 부수는 것 "
                  "하나뿐이다 — 1차를 다시 쓰거나 요약·반복하면 그 답은 실패다. 1차가 아무리 그럴듯하고 "
                  "자신있게 써도 휘둘리지 말고, 모든 단정을 [근거]와 일일이 대조해 깨라. 칭찬·맞장구·동의 "
                  "금지. 너도 없는 숫자를 새로 지어내지 마라. 한국어, 짧고 매섭게.")


def _think_strip(s):
    s = re.sub(r"<think>.*?</think>", "", s or "", flags=re.S)
    s = re.sub(r"^.*?</think>", "", s, flags=re.S)
    return s.strip()


def analyze(query, model, keep_alive="5m", verbose=True, model_audit=None):
    """수집 + 판단 전체. comet.py 가 model/keep_alive 를 넘긴다. 반환 {ok, answer, ...}.
       model_audit 를 주면 2차(감사·역발상)만 다른 모델로 돌린다(하이브리드)."""
    tr = _trace.Trace(verbose, title=f"종목·테마 분석  ·  {query}")
    ev = gather(query, verbose=verbose, trace=tr)
    return judge(ev, model, model_audit, keep_alive, verbose=verbose, trace=tr)


def judge(ev, model1, model2=None, keep_alive="5m", verbose=True, trace=None):
    """이미 수집한 ev(gather 결과)로 2패스만 실행 — 비교 시 같은 근거 재사용용.
       model1=1차(컨센서스·2차효과), model2=2차(감사·역발상, 없으면 model1)."""
    tr = trace or _trace.Trace(verbose, title="종목·테마 분석")
    model2 = model2 or model1
    evidence = _evidence(ev)

    # 수집한 자료를 정세 로그에 자동 적재(신뢰 필터·중복방지·날짜는 marketlog가 처리)
    try:
        topic = ev.get("ticker") or ev.get("query", "")
        lg = marketlog.log_research(topic, ev.get("main"))
        tr.step("신뢰 필터 · 정세로그",
                f"채택 {lg['logged']} · 중복 {lg['dup']} · 걸러냄 {lg['rejected']}")
        if lg["rejects"]:
            tr.warn(lg["rejects"][0])
    except Exception as e:
        tr.warn(f"정세로그 적재 건너뜀: {e}")

    tr.step("컨센서스 · 2차효과 추론",
            f"{model1} · 실데이터 {'반영' if ev['px'] else '—'}")

    # 강제 규칙·형식은 유저 메시지 '끝'에 둔다(모델은 최신 지시를 가장 잘 따른다).
    reg = _regime(ev.get("px") or {})
    if reg:
        preempt = (
            "**💵 선반영 점검** — ★오늘 실측 국면(파이썬 계산값)은 [" + reg + "] 다. "
            "블로그·기사가 과거 시점 기준으로 '강세·몇 배 상승·이평선 상회'라 말해도, **현재 위치·추세는 이 실측값이 정답**이다"
            "(웹 자료의 과거 서술을 현재로 착각하지 마라). 이걸 출발점으로 컨센서스가 가격에 얼마나 선반영됐는지 단정하고, "
            "컨센서스(예: 강세)와 실측 국면(예: 바닥권·하락추세)이 어긋나면 그 모순을 반드시 못박아라.\n")
    else:
        preempt = ("**💵 선반영 점검** — 가격 실데이터 없음 → 웹 자료의 추세 언급으로 신중히 판단"
                   "(과거 시점 서술일 수 있으니 주의).\n")
    fin = ev.get("fin") or {}
    if fin.get("ok"):
        vbits = []
        for k, lab in (("fwd_pe", "선행PER"), ("trail_pe", "PER"), ("peg", "PEG"),
                       ("target_mean", "애널 목표가 평균")):
            if fin.get(k):
                vbits.append(f"{lab} {fin[k]}")
        if vbits:
            preempt += ("   ※ 실측 밸류에이션(Yahoo, 무키): " + " · ".join(vbits)
                        + ". 블로그·기사가 다른 PER(예: '60배')을 말해도 **이 실측값을 써라**(블로그 숫자 금지).\n")
    _px = ev.get("px") or {}
    if _px.get("rsi14") is not None or _px.get("atr14") is not None:
        preempt += ("   ※ 셋업 지표(RSI·ATR·2×ATR 손절선·50일선)가 '가격·추세' 블록에 계산돼 있다 — "
                    "진입 타이밍(과매수/과매도·눌림)과 손절폭은 그 계산값을 인용해 짚어라(임의 숫자 금지).\n")
    user1 = (
        "아래 [근거]만 보고 분석해라. 머릿속 지식으로 시총·매출·시장규모·점유율 숫자를 쓰면 실패다.\n\n"
        "[근거]\n" + evidence + "\n\n"
        "── 위 근거만 써서, 이 형식으로 날카롭게(핵심만, 군더더기 0):\n"
        "**📊 컨센서스(시장이 한목소리로 믿는 것)** — 지금 증권가·시장이 믿는 스토리를 한 줄로 단정 + [본N]. 강세/약세/엇갈림 중 무엇.\n"
        + preempt +
        "**🔗 2차 파급(구체적으로)** — 이게 움직이면 연쇄로 움직일 대상 2~3개를 **[회사/종목 이름] + 방향(↑/↓) + 이유 한 줄**로. **실제 상장 회사·종목·ETF만**(뉴스레터·블로그·유튜브·정보채널 이름 금지). '수혜 가능' 같은 막연한 말 금지. 자료에 이름 있으면 [본N]/[파N], 없으면 일반 메커니즘으로라도 구체 종목명을 대라.\n"
        "**🎯 잠정 방향** — 강세/약세/박스 + 확신도 NN% + 근거 2개[본N]. 실측 국면과 컨센서스가 어긋나면 방향·확신도에 반영해라. 다음 단계 검증관이 이걸 공격한다.\n"
        "※ '펀더멘털·실적' 블록(매출성장·마진·선행PER·PEG·애널리스트 목표가·어닝 서프라이즈)이 있으면 "
        "밸류에이션 부담과 실적 모멘텀 판단에 적극 인용해라(블로그 풍문보다 이 실데이터 우선).\n"
        "근거에 없는 숫자는 절대 쓰지 마라. 모르면 '자료에 없음'.")

    # 1차: 컨센서스 + 2차효과
    try:
        r1 = _AC.chat(
            model=model1, keep_alive=keep_alive,
            options={"temperature": 0.1, "num_predict": 1300},
            messages=[{"role": "system", "content": CONSENSUS_SYS},
                      {"role": "user", "content": user1}],
        )
        pass1 = _think_strip(r1["message"]["content"])
    except Exception as e:
        return {"ok": False, "msg": f"1차 분석 실패: {e}"}

    tr.think("역발상 · 사실 감사  —  1차를 의심하고 출처와 대조")

    user2 = (
        "[근거]\n" + evidence + "\n\n"
        "[1차 분석]\n" + pass1 + "\n\n"
        "── 1차를 다시 쓰지 마라. 감사관으로서 이 형식으로 매섭게:\n"
        "※ 철칙: 너(감사관)의 반박·숫자도 모두 [근거]의 접근가능한 [출처N]에 기대야 한다. 출처 못 대는 "
        "숫자나 주장으로 반박하지 마라 — 그건 ✅이나 확정 근거로 쓰지 말고 ❓미확인에만 적어라. 그리고 사실은 "
        "정황상 앞뒤가 맞아야 한다(다른 근거와 모순되면 '정황 불일치'라 밝혀라).\n"
        "**🔎 사실 감사** — 1차의 숫자·단정을 [근거]와 하나씩 대조. **단, [근거]의 '가격·추세'·'기술적 "
        "국면'·'펀더멘털·실적' 블록은 Yahoo API/실계산값이다 — 이건 사실로 받아들이고 네 머릿속 숫자(PER 등)로 "
        "반박하지 마라(네 기억이 오래됐을 수 있다).** 감사 대상은 1차가 그 실측 블록이나 [본N]/[파N] '어디에도 없는' "
        "숫자·주장을 지어낸 경우다 — \"1차: 'X' → 근거 어디에도 없음\"으로 집어내라(특히 시총·시장규모·점유율 등 웹 "
        "출처에 없는 값). 깨끗하면 '근거 일탈 없음'.\n"
        "**🩸 컨센서스가 틀릴 지점** — 시장이 다 저렇게 믿을 때, 그 믿음이 깨지는 가장 그럴듯한 시나리오 1~2개"
        "(무엇이/어떤 수치가 어긋나면 무너지나). 선반영됐으면 '이미 반영, 추가 상승 여력 약함'을, 군중 과열이면 "
        "그 위험을 못박아라. 추세가 정당하면 '역발상 부적절, 추세 유효'라 솔직히.\n"
        "※ 근거의 '시장 군중심리(CNN Fear&Greed)'를 역발상 저울로 써라 — 군중이 **극단적 공포(extreme fear)**면 "
        "과매도→남들이 못 사는 지금이 기회일 수 있고, **극단적 탐욕(extreme greed)**이면 과열→되돌림 위험. "
        "단 이건 '전체 시장' 심리이니 개별 종목 펀더멘털·국면과 구분해서 가중치로만 쓰고, 세부지표(풋콜·정크본드·안전자산 등)가 "
        "한 방향으로 쏠렸는지도 봐라(쏠릴수록 역발상 신호 강함).\n"
        "**🧭 최종 판단** — 셋으로 분리: ✅확인된 사실(각 [출처N] 있는 것만) / 🤔내 추론(\"제 추론으로는~\"+근거"
        "+확신도 확실·높음·추측) / ❓미확인(자료로 안 끝난 것+어느 원천 더 볼지).\n"
        "**⚡ 한 줄 결론** — 방향 + 확신도% + **뒤집을 트리거 1개**(예: 'VIX 25 돌파', 'X 가이던스 하향'). "
        "호윤 보유에 닿으면 더 담을지/줄일지/갈아탈지 직접 짚어라(근거에 '호윤 실제 보유 현황'이 "
        "있으면 그 종목·수량·평단·손익 기준으로 구체적으로, 없으면 일반 보유 엔비디아·TQQQ·우주株 기준).")

    # 2차: 역발상 + 사실 감사 (이중 판단)
    try:
        r2 = _AC.chat(
            model=model2, keep_alive=keep_alive,
            options={"temperature": 0, "num_predict": 1800},
            messages=[{"role": "system", "content": CHALLENGER_SYS},
                      {"role": "user", "content": user2}],
        )
        pass2 = _think_strip(r2["message"]["content"])
    except Exception as e:
        # 1차라도 살려서 반환
        return {"ok": True, "answer": pass1 + "\n\n(2차 검증 실패: " + str(e) + ")",
                "partial": True}

    # 실계산 가격은 파이썬이 박아 그라운딩 보장(LLM 무시해도 진짜 숫자는 보인다)
    answer = ("### 📈 실데이터 (Yahoo, 계산값)\n" + _px_lines(ev["px"])
              + (("\n\n" + financials.fmt(ev["fin"]))
                 if ev.get("fin") and ev["fin"].get("ok") else "")
              + "\n\n### 🧠 1차 분석 (컨센서스 + 2차효과)\n" + pass1
              + "\n\n### 🔴 2차 검증 (역발상 + 사실 감사)\n" + pass2
              + "\n\n### 🔗 출처 (검토용 링크)\n" + _sources_footer(ev))
    tr.done("분석 완료")
    return {"ok": True, "answer": answer, "pass1": pass1, "pass2": pass2,
            "ticker": ev["ticker"], "n_sources": ev["main"].get("count", 0)}


def dispatch(action, args):
    if action == "analyze_stock":
        return analyze(args.get("query", ""),
                       args.get("model", ANALYST_MODEL),   # 기본 27b (정확·날카로움 우선)
                       args.get("keep_alive", "5m"),
                       model_audit=args.get("model_audit"))
    return {"ok": False, "msg": f"알 수 없는 분석 동작: {action}"}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    q = " ".join(sys.argv[1:]) or "엔비디아 지금 사도 될까"
    out = analyze(q, ANALYST_MODEL)
    print("\n" + "=" * 60)
    print(out.get("answer") or out.get("msg"))
