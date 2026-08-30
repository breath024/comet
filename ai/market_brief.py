# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 일일 미국 증시 브리핑 (좁게: 지수·반도체·특징주)
#
#  목표 = "핵심 포인트 + 해석"식 종합 브리핑(SPY 커뮤니티 정리글 같은 것).
#  그라운딩 철학(환각 차단):
#   · 지수·종목 등락률 = prices.py(Yahoo API 직접) → 숫자는 정확, LLM 안 거침=날조 0.
#   · 사건·인과       = 구글뉴스 RSS(최근·날짜 있음)를 1차 소스로 → LLM 2패스 종합·해석.
#   · 출처 없는 구체 수치 금지, 해석은 '추정'으로 표시.
#
#  ★ 사건 소싱(2026-06): DuckDuckGo는 오늘 마감글 대신 홈페이지·옛 블로그(stale)를 줘서
#    9월 옛 사건이 오늘에 섞여 들어오던 문제가 있었음. → news.py(RSS)로 교체:
#    날짜(_ts)가 있어 FRESH_DAYS 내 기사만 추리고, evidence에 오늘 날짜를 박아 옛 사건 차단.
#    뉴스가 빈약할 때만 web 검색을 '날짜 미확인 보조'로 덧붙임.
#  proactive 모드가 매일 미장 마감 후(한국 아침) 호출 → 텔레그램 푸시.
# ═══════════════════════════════════════════════════════════════
import re
import time
from datetime import datetime

import ollama
import prices
import web
import news

BRIEF_MODEL = "gemma4:26b"            # 종합·해석은 신중하게(정확>속도)
_AC = ollama.Client(timeout=600)      # 2패스 27b — 얼어붙음 대비 긴 타임아웃

INDICES = [("^DJI", "다우"), ("^IXIC", "나스닥"),
           ("^GSPC", "S&P500"), ("^SOX", "필라델피아 반도체")]

# 좁은 범위 = 저 정리글의 단골(반도체·AI·메가캡)
MOVERS = [("NVDA", "엔비디아"), ("AMD", "AMD"), ("TSM", "TSMC"), ("MU", "마이크론"),
          ("AVGO", "브로드컴"), ("INTC", "인텔"), ("QCOM", "퀄컴"), ("ARM", "ARM"),
          ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("AMZN", "아마존"),
          ("TSLA", "테슬라"), ("GOOGL", "알파벳"), ("META", "메타")]

# 사건 소싱 1차 = 구글뉴스 RSS(최근·날짜 있음)
NEWS_QUERIES = [("미국 증시 마감", "ko"), ("미국 반도체 주가", "ko"),
                ("US stock market close", "en"), ("semiconductor stocks", "en")]
FRESH_DAYS = 2                                # 최근 N일 기사만(미장 시차 커버)
DEEP_K = 4                                    # 상위 헤드라인 N개는 본문까지 긁어 해석 깊이 확보
WEB_FALLBACK = ("미국 증시 마감 특징주", "kr-ko")   # 뉴스가 빈약할 때만 보조


def _strip_think(t):
    t = re.sub(r"<think>.*?</think>", "", t or "", flags=re.S)
    t = re.sub(r"^.*?</think>", "", t, flags=re.S)
    return t.strip()


# ── 사건: 최근(FRESH_DAYS 내) 구글뉴스 기사만 ────────────────────
def _fresh_news():
    cutoff = time.time() - FRESH_DAYS * 86400
    items, seen = [], set()
    for kw, lang in NEWS_QUERIES:
        try:
            for it in news.fetch_keyword(kw, lang=lang, limit=12):
                if it.get("_ts", 0) < cutoff:          # 오래된 기사 = stale, 제외
                    continue
                key = (it.get("title") or "")[:48]
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(it)
        except Exception:
            pass
    items.sort(key=lambda x: x.get("_ts", 0), reverse=True)
    return items[:16]


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


# ── 수집: 그라운딩 숫자(prices) + 최근 사건(news, 폴백 web) ───────
def gather():
    idx_lines, mov_lines = [], []
    for sym, name in INDICES:
        try:
            r = prices.stock(sym)
            if r.get("ok") and r.get("change_pct") is not None:
                idx_lines.append(f"{name} {r['change_pct']:+.2f}% ({r['price']:,.2f})")
        except Exception:
            pass
    for sym, name in MOVERS:
        try:
            r = prices.stock(sym)
            if r.get("ok") and r.get("change_pct") is not None:
                mov_lines.append(f"{name}({sym}) {r['change_pct']:+.2f}%")
        except Exception:
            pass
    news_items = _deepen(_fresh_news())
    web_sources = []
    if len(news_items) < 3:                  # 최근 뉴스가 빈약하면 웹 보조(날짜 미확인)
        try:
            res = web.research(WEB_FALLBACK[0], n_sources=4, kl=WEB_FALLBACK[1])
            if res.get("ok"):
                web_sources = res.get("sources", [])
        except Exception:
            pass
    sentiment = prices.fear_greed("")        # CNN 미장 군중심리(종합+세부)
    return idx_lines, mov_lines, news_items, web_sources, sentiment


def _evidence(idx_lines, mov_lines, news_items, web_sources, sentiment, today):
    ev = [f"[오늘 날짜: {today}. 아래 기사 중 이 날짜와 동떨어진 옛 사건은 절대 쓰지 마라]",
          "",
          "[지수 — Yahoo 실측. 이 숫자만 정확한 값으로 인용]",
          " · ".join(idx_lines) or "(지수 수집 실패)",
          "",
          "[반도체·특징주 등락 — Yahoo 실측. 이 숫자만 정확]",
          " · ".join(mov_lines) or "(종목 수집 실패)"]
    if sentiment and sentiment.get("ok"):
        ev += ["",
               "[시장 군중심리 — CNN Fear&Greed(역발상 신호). 거시·수급 해석에 '심리' 맥락으로 반영]",
               prices.fmt(sentiment)]
    ev += ["",
           "[오늘 시장 기사 — 구글뉴스(최신순, 날짜 표시). 이 사실만 해석 근거로 인용]"]
    if news_items:
        for it in news_items:
            ts = it.get("_ts", 0)
            d = time.strftime("%m-%d", time.localtime(ts)) if ts else "??"
            ev.append(f"({d}) {it.get('title','')} — {it.get('source','')}")
            body = it.get("body")
            if body:        # 상위 기사 = 본문(해석 깊이의 핵심). 메뉴/네비 잡음 섞일 수 있음.
                ev.append(f"   [본문] {body}")
            else:
                sm = (it.get("summary") or "").strip()
                if sm:
                    ev.append(f"   {sm[:160]}")
    else:
        ev.append("(최근 기사 수집 실패)")
    if web_sources:
        ev.append("")
        ev.append("[보조 웹 — 날짜 미확인, 참고만(단정 근거로 쓰지 마라)]")
        for s in web_sources[:4]:
            ev.append(f"· {s.get('title','')}: {(s.get('text') or s.get('snippet') or '')[:200]}")
    return "\n".join(ev)


SYNTH_SYS = (
    "너는 미국 증시 일일 브리핑 애널리스트다. 주어진 [근거]만으로 한국어 브리핑을 쓴다.\n"
    "[철칙]\n"
    "- 지수·종목 등락률(%)은 [근거]의 Yahoo 실측 숫자만 그대로 써라. 네 기억 속 숫자 절대 금지.\n"
    "- 사건·인과는 [근거]의 '오늘 시장 기사'에 실제로 있는 것만 써라. 오늘 날짜와 동떨어진 옛 사건은 쓰지 마라.\n"
    "- 옵션 미결제·펀드 매도 규모·목표가 같은 구체 수치는 기사에 있을 때만. 없으면 지어내지 말고 정성적으로.\n"
    "- '[본문]'이 붙은 기사는 그 안의 구체 이유·맥락을 적극 캐서 '왜'를 깊게 써라(메뉴·네비 잡음은 무시). 본문 없는 건 제목 수준으로만.\n"
    "- 사실(기사 근거)과 해석(네 추론)을 구분하고, 해석엔 '~로 해석된다/추정된다'를 붙여라.\n"
    "- '시장 군중심리(CNN Fear&Greed)'가 근거에 있으면 거시·수급 흐름 해석에 녹여라 — 극단 공포=과매도·역발상 매수 분위기, "
    "극단 탐욕=과열·되돌림 경계. 단 지수·종목 숫자처럼 단정 사실로 쓰지 말고 '심리/분위기' 맥락으로, 세부지표 쏠림(풋콜·정크본드·안전자산 등)도 짚어라.\n"
    "- 근거가 빈약하면 무리해서 채우지 말고 있는 만큼만 충실히(짧아도 됨).\n"
    "[형식]\n"
    "📊 <b>미 증시 핵심 포인트</b> — 거시·수급 흐름 위주, 각 줄 '제목: 한두 줄 해석'.\n"
    "🔎 <b>특징 종목</b> — 크게 움직인 종목 위주 '종목 ±%: 왜(기사 근거)'.\n"
    "끝에 '지수 마감: 다우 ±% · 나스닥 ±% · S&P ±% · 필반 ±%' 한 줄.")

AUDIT_SYS = (
    "너는 냉정한 사실 감사관이다. [브리핑 초안]을 [근거]와 대조해 고쳐 최종본만 낸다.\n"
    "- [근거]에 없는 구체 숫자·단정·옛 사건은 '추정'으로 낮추거나 삭제.\n"
    "- 지수·종목 등락률이 [근거]와 다르면 [근거] 값으로 교정.\n"
    "- 인과를 과하게 단정한 곳('때문에')은 '~영향으로 보임'으로 완화.\n"
    "- 형식·이모지·길이는 유지. 감사 코멘트 없이 다듬어진 최종 브리핑만 출력.")


def generate(model=BRIEF_MODEL, verbose=False):
    """수집 → 종합(1차) → 사실감사(2차) → 최종 브리핑 텍스트."""
    idx_lines, mov_lines, news_items, web_sources, sentiment = gather()
    if not idx_lines and not mov_lines:
        return {"ok": False, "msg": "시세 수집 실패 — 브리핑 보류(잠시 후 재시도)"}
    today = datetime.now().strftime("%Y-%m-%d")
    evidence = _evidence(idx_lines, mov_lines, news_items, web_sources, sentiment, today)
    if verbose:
        print(evidence, "\n", "=" * 40)
    try:
        r1 = _AC.chat(model=model, keep_alive="2m",
                      options={"temperature": 0.2, "num_predict": 2000},
                      messages=[{"role": "system", "content": SYNTH_SYS},
                                {"role": "user", "content": "[근거]\n" + evidence}])
        draft = _strip_think(r1["message"].get("content", ""))
    except Exception as e:
        return {"ok": False, "msg": f"종합 단계 실패: {e}"}
    if not draft:
        return {"ok": False, "msg": "종합 결과가 비어 있음"}
    try:
        r2 = _AC.chat(model=model, keep_alive="2m",
                      options={"temperature": 0, "num_predict": 2000},
                      messages=[{"role": "system", "content": AUDIT_SYS},
                                {"role": "user",
                                 "content": "[근거]\n" + evidence + "\n\n[브리핑 초안]\n" + draft}])
        final = _strip_think(r2["message"].get("content", "")) or draft
    except Exception:
        final = draft        # 감사 실패해도 1차 초안은 살림
    footer = (f"\n\n🔗 기사 {len(news_items)}건"
              + (f"(+웹 {len(web_sources)})" if web_sources else "")
              + f" · 숫자=Yahoo 실측 · {today}")
    return {"ok": True, "text": final + footer, "news": news_items,
            "web": web_sources, "indices": idx_lines, "movers": mov_lines}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    r = generate(verbose=True)
    print(r.get("text") if r.get("ok") else "실패: " + r.get("msg", "?"))
