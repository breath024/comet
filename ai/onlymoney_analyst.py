# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  Only Money 9단 파이프라인 분석 — COMET 연동
#   snapshot.json (Only Money에서 내보낸 포트·관심종목·저널) +
#   prompts.json (9단 레이어 정의) 를 읽어 COMET이 직접 분석 실행.
#   각 레이어 출력은 스트리밍으로 콘솔에 실시간 표시.
# ═══════════════════════════════════════════════════════════════
import os, json, math, threading
import urllib.request

HOME          = os.path.expanduser("~")
SNAPSHOT_PATH = os.path.join(HOME, "iCloudDrive", "Only Money", "snapshot.json")
PROMPTS_PATH  = os.path.join(HOME, "iCloudDrive", "Only Money", "prompts.json")

MODEL   = "gemma3:27b"   # analyst.py와 동일 — 신중한 추론 모델
TIMEOUT = 300            # 레이어당 최대 대기(초)
UA      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) COMET/1.0"

import ollama
_client = ollama.Client(timeout=TIMEOUT)


# ── HTTP ─────────────────────────────────────────────────────
def _get(url, t=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=t) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ── 기술지표 계산 ──────────────────────────────────────────────
def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [max(-d, 0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    return 100.0 if avg_l == 0 else 100 - (100 / (1 + avg_g / avg_l))


def _atr(closes, highs, lows, period=14):
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i-1]),
               abs(lows[i]  - closes[i-1]))
           for i in range(1, len(closes))]
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for t in trs[period:]:
        atr = (atr * (period - 1) + t) / period
    return atr


# ── 티커 데이터 (Yahoo Finance 1y 일봉) ──────────────────────
def _fetch_ticker(ticker):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           "?interval=1d&range=1y")
    try:
        j    = _get(url)
        res  = j["chart"]["result"][0]
        meta = res["meta"]
        q    = res["indicators"]["quote"][0]

        rows = [(c, h, l, v)
                for c, h, l, v in zip(q.get("close",[]), q.get("high",[]),
                                      q.get("low",[]),   q.get("volume",[]))
                if c is not None and h is not None and l is not None]
        if len(rows) < 20:
            return {"ticker": ticker, "error": "데이터 부족"}

        cls = [r[0] for r in rows]
        hig = [r[1] for r in rows]
        low = [r[2] for r in rows]
        vol = [r[3] for r in rows if r[3] is not None]

        price    = meta.get("regularMarketPrice") or cls[-1]
        prev     = meta.get("chartPreviousClose") or cls[-2]
        chg_pct  = (price - prev) / prev * 100 if prev else 0

        rsi_val  = _rsi(cls)
        atr_val  = _atr(cls, hig, low)
        atr_pct  = atr_val / price * 100 if atr_val and price else None

        sma50    = sum(cls[-50:])  / 50  if len(cls) >= 50  else None
        sma200   = sum(cls[-200:]) / 200 if len(cls) >= 200 else None
        s50_dev  = (price - sma50)  / sma50  * 100 if sma50  else None
        s200_dev = (price - sma200) / sma200 * 100 if sma200 else None

        vol_avg  = sum(vol[-21:-1]) / 20 if len(vol) >= 21 else None
        vol_rat  = vol[-1] / vol_avg if vol_avg and vol else None

        hi52  = max(hig)
        lo52  = min(low)
        pos52 = (price - lo52) / (hi52 - lo52) * 100 if hi52 != lo52 else 50

        return {
            "ticker":    ticker,
            "price":     round(price, 3),
            "chg_pct":   round(chg_pct, 2),
            "rsi":       round(rsi_val, 1) if rsi_val is not None else None,
            "atr":       round(atr_val, 3) if atr_val is not None else None,
            "atr_pct":   round(atr_pct, 2) if atr_pct is not None else None,
            "sma50_dev": round(s50_dev, 1) if s50_dev is not None else None,
            "sma200_dev":round(s200_dev, 1) if s200_dev is not None else None,
            "vol_ratio": round(vol_rat, 2) if vol_rat is not None else None,
            "pos52":     round(pos52, 1),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)[:80]}


def _fear_greed():
    try:
        j = _get("https://api.alternative.me/fng/?limit=1", t=10)
        d = j["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return None


# ── 병렬 시세 수집 ────────────────────────────────────────────
def _fetch_all(tickers):
    results = {}
    lock = threading.Lock()

    def worker(t):
        d = _fetch_ticker(t)
        with lock:
            results[t] = d

    threads = [threading.Thread(target=worker, args=(t,)) for t in tickers]
    for th in threads: th.start()
    for th in threads: th.join(timeout=20)
    return results


# ── 컨텍스트 빌드 ─────────────────────────────────────────────
def _build_context(snapshot, quotes, fg):
    portfolio  = snapshot.get("portfolio", [])
    watchlist  = snapshot.get("watchlist", [])
    journal    = snapshot.get("journal",   [])

    lines = []

    # F&G
    if fg:
        lines.append(f"## 📊 공포탐욕지수 (Fear & Greed)")
        lines.append(f"현재값: {fg['value']} / 100  ({fg['label']})")
        lines.append("")

    # 실시간 시세 + 기술지표
    lines.append("## 📈 실시간 시세 + 기술지표")
    lines.append("(RSI·ATR·이평선 이격은 Yahoo Finance 1년 일봉 실계산값. VIX=^VIX)")
    lines.append("")

    for t, d in quotes.items():
        if "error" in d:
            lines.append(f"- {t}: 조회 실패 ({d['error']})")
            continue
        parts = [f"{t}  현재가 ${d['price']}  등락 {d['chg_pct']:+.2f}%"]
        if d.get("rsi") is not None:
            parts.append(f"RSI {d['rsi']}")
        if d.get("sma50_dev") is not None:
            parts.append(f"50일선 {d['sma50_dev']:+.1f}%")
        if d.get("sma200_dev") is not None:
            parts.append(f"200일선 {d['sma200_dev']:+.1f}%")
        if d.get("atr_pct") is not None:
            parts.append(f"ATR {d['atr_pct']:.2f}% (${d['atr']:.3f})")
        if d.get("vol_ratio") is not None:
            vr = d["vol_ratio"]
            flag = "🔥" if vr >= 1.5 else ("💤" if vr < 0.7 else "")
            parts.append(f"거래량배수 {vr}x{flag}")
        if d.get("pos52") is not None:
            parts.append(f"52주 위치 {d['pos52']:.0f}%")
        lines.append("  " + " · ".join(parts))
    lines.append("")

    # 포트폴리오
    if portfolio:
        lines.append("## 💼 포트폴리오 (보유종목)")
        total_cost = sum((p.get("shares", 0) or 0) * (p.get("avgCost", 0) or 0)
                        for p in portfolio)
        tickers_by_weight = {}
        for p in portfolio:
            t    = (p.get("ticker") or "").upper()
            cost = (p.get("shares", 0) or 0) * (p.get("avgCost", 0) or 0)
            tickers_by_weight[t] = cost / total_cost * 100 if total_cost else 0

        for p in portfolio:
            t    = (p.get("ticker") or "").upper()
            sh   = p.get("shares", 0) or 0
            avg  = p.get("avgCost", 0) or 0
            wt   = tickers_by_weight.get(t, 0)
            q    = quotes.get(t, {})
            price = q.get("price")
            pnl_pct = (price - avg) / avg * 100 if price and avg else None
            row = f"  {t}: {sh}주 · 평단 ${avg:.2f} · 비중 {wt:.1f}%"
            if pnl_pct is not None:
                row += f" · 손익 {pnl_pct:+.1f}%"
            lines.append(row)

        # 집중도 최대
        if tickers_by_weight:
            top = max(tickers_by_weight, key=tickers_by_weight.get)
            lines.append(f"  ⚠️ 최대 집중도: {top} {tickers_by_weight[top]:.1f}%")
        lines.append("")

    # 관심종목
    if watchlist:
        lines.append("## 👀 관심종목")
        lines.append("  " + " / ".join(watchlist))
        lines.append("")

    # 매매 저널 (최근 14건)
    recent_j = sorted(journal, key=lambda x: x.get("date",""), reverse=True)[:14]
    if recent_j:
        lines.append("## 📓 호윤 최근 매매 저널 (최신 14건)")
        for j_entry in recent_j:
            date   = j_entry.get("date", "?")
            ticker = (j_entry.get("ticker") or "").upper()
            action = j_entry.get("action", "?")
            price  = j_entry.get("price", "?")
            shares = j_entry.get("shares", "?")
            reason = (j_entry.get("reason") or "")[:60]
            lines.append(f"  {date} {ticker} {action} {shares}주 @${price}  {reason}")
        lines.append("")

    return "\n".join(lines)


# ── 9단 파이프라인 실행 ───────────────────────────────────────
def _run_layer(layer, base_context, accumulated, print_fn):
    sp = layer["systemPrompt"]
    icon = layer.get("icon", "")
    name = layer["name"]

    header = f"\n{'═'*60}\n{icon} {name}\n{'═'*60}"
    print_fn(header)

    user_msg = (
        f"{base_context}\n\n"
        f"{'─'*40}\n이전 분석 레이어 누적:\n{'─'*40}\n{accumulated}"
        if accumulated else base_context
    )

    messages = [
        {"role": "system",  "content": sp},
        {"role": "user",    "content": user_msg},
    ]

    chunks = []
    try:
        for part in _client.chat(model=MODEL, messages=messages, stream=True):
            tok = part.get("message", {}).get("content", "")
            if tok:
                print_fn(tok, end="")
                chunks.append(tok)
        print_fn("")
    except Exception as e:
        err = f"\n[레이어 오류] {e}"
        print_fn(err)
        chunks.append(err)

    return "".join(chunks)


# ── 공개 진입점 ───────────────────────────────────────────────
def run(print_fn=None):
    """9단 파이프라인 실행. print_fn 없으면 print() 사용. 전체 텍스트 반환."""
    if print_fn is None:
        def print_fn(s="", end="\n"):
            print(s, end=end, flush=True)

    # 1. snapshot 확인
    if not os.path.isfile(SNAPSHOT_PATH):
        msg = (f"snapshot.json 을 찾을 수 없어. ({SNAPSHOT_PATH})\n"
               "Only Money 열고 데이터 탭 → 'COMET 스냅샷 저장' 버튼 눌러줘.")
        print_fn(msg)
        return msg

    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshot = json.load(f)

    snapshot_age = ""
    try:
        from datetime import datetime, timezone
        exp = snapshot.get("exportedAt", "")
        if exp:
            dt  = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_m = int((now - dt).total_seconds() / 60)
            snapshot_age = f" (스냅샷 {age_m}분 전)"
    except Exception:
        pass

    # 2. prompts 로드
    if not os.path.isfile(PROMPTS_PATH):
        msg = f"prompts.json 을 찾을 수 없어. ({PROMPTS_PATH})"
        print_fn(msg)
        return msg

    with open(PROMPTS_PATH, encoding="utf-8") as f:
        prompts = json.load(f)
    layers = prompts.get("layers", [])

    # 3. 티커 목록 수집
    portfolio = snapshot.get("portfolio", [])
    watchlist = snapshot.get("watchlist", [])
    pf_tickers = list({(p.get("ticker") or "").upper() for p in portfolio if p.get("ticker")})
    wl_tickers = list({t.upper() for t in watchlist if t})
    all_tickers = list(dict.fromkeys(pf_tickers + wl_tickers + ["^VIX"]))

    print_fn(f"Only Money 9단 파이프라인 분석{snapshot_age}")
    print_fn(f"종목 {len(all_tickers)}개 시세 수집 중... ({', '.join(all_tickers[:8])}{'...' if len(all_tickers) > 8 else ''})")

    # 4. 병렬 시세 수집
    quotes = _fetch_all(all_tickers)
    ok  = sum(1 for d in quotes.values() if "error" not in d)
    err = len(quotes) - ok
    print_fn(f"시세 수집 완료: {ok}개 성공 / {err}개 실패")

    # 5. F&G
    print_fn("공포탐욕지수 조회 중...")
    fg_cache = snapshot.get("fg_cache", {})
    if fg_cache and fg_cache.get("data"):
        try:
            d = fg_cache["data"]
            fg = {"value": int(d.get("score") or d.get("fear_and_greed", {}).get("score", 0)),
                  "label": d.get("rating") or d.get("fear_and_greed", {}).get("rating", "")}
            print_fn(f"  F&G: {fg['value']} ({fg['label']}) — 스냅샷 캐시 사용")
        except Exception:
            fg = _fear_greed()
    else:
        fg = _fear_greed()
    if fg:
        print_fn(f"  F&G: {fg['value']} / 100  ({fg['label']})")

    # 6. VIX 표시
    vix_d = quotes.get("^VIX", {})
    if "error" not in vix_d:
        print_fn(f"  VIX: {vix_d.get('price')}  등락 {vix_d.get('chg_pct', 0):+.2f}%")

    # 7. 컨텍스트 빌드
    context = _build_context(snapshot, quotes, fg)

    # 8. 9단 레이어 순차 실행
    all_text = [f"Only Money 9단 파이프라인 분석{snapshot_age}\n"]
    accumulated = ""

    for i, layer in enumerate(layers):
        layer_out = _run_layer(layer, context, accumulated, print_fn)
        section = f"\n{layer.get('icon','')} {layer['name']}\n{layer_out}"
        all_text.append(section)
        accumulated += section

    full = "\n".join(all_text)
    print_fn(f"\n{'═'*60}")
    print_fn("분석 완료.")
    return full
