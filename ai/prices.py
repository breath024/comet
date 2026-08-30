# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 시세 엔진 — 라이브 환율·코인·주가·공포탐욕지수
#   무키 JSON API를 파이썬이 직접 호출 → 숫자는 API가 준 그대로.
#   LLM 안 거침 = 날조 불가능(스크래핑+모델 추론이 환율 1,365 날조하던 문제의 해결책).
#   소스: Yahoo Finance(환율/주가), CoinGecko·Upbit(코인), alternative.me(공포탐욕).
#         alternative.me 는 Only Money 대시보드도 쓰는 검증된 소스.
#   의존성 0(urllib만). comet.py 의 도구 디스패치 규약({ok,...})을 따른다.
# ═══════════════════════════════════════════════════════════════
import re
import json
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) COMET/1.0"


def _get_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


# ── 환율 (Yahoo Finance) ─────────────────────────────────────────
# 한국어/약칭 → Yahoo 심볼. 심볼별 (이름, 단위, 표시통화)
FX_SYMS = {
    "KRW=X":    ("원·달러 환율", "1달러", "원"),
    "JPYKRW=X": ("원·엔 환율",   "1엔",   "원"),
    "EURKRW=X": ("원·유로 환율", "1유로", "원"),
    "CNYKRW=X": ("원·위안 환율", "1위안", "원"),
    "GBPKRW=X": ("원·파운드 환율", "1파운드", "원"),
    "EURUSD=X": ("유로·달러",    "1유로", "달러"),
}
# 주의: 구체 통화를 먼저, "환율"·"달러" 같은 포괄어(원달러 기본)는 맨 뒤.
FX_ALIASES = [
    (("엔화", "원엔", "엔환율", "100엔", "엔 얼마", "jpykrw"), "JPYKRW=X"),
    (("유로달러", "eurusd", "유로 달러"), "EURUSD=X"),
    (("유로", "원유로", "유로환율"), "EURKRW=X"),
    (("위안", "원위안", "위안환율"), "CNYKRW=X"),
    (("파운드", "원파운드"), "GBPKRW=X"),
    (("원달러", "달러원", "달러환율", "달러 환율", "usdkrw", "usd/krw", "달러얼마", "달러 얼마", "환율"), "KRW=X"),
]


def _yahoo(symbol, kind):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           "?interval=1d&range=1d")
    try:
        j = _get_json(url)
        meta = j["chart"]["result"][0]["meta"]
    except Exception as e:
        return {"ok": False, "msg": f"{kind} 조회 실패: {e}"}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return {"ok": False, "msg": f"{kind} 값이 비어 있음(심볼 {symbol})."}
    chg = (price - prev) if prev else None
    pct = (chg / prev * 100) if (chg is not None and prev) else None
    return {"ok": True, "kind": kind, "symbol": symbol,
            "price": price, "currency": meta.get("currency"),
            "prev_close": prev,
            "change": round(chg, 4) if chg is not None else None,
            "change_pct": round(pct, 2) if pct is not None else None,
            "source": "Yahoo Finance"}


def fx(query):
    q = (query or "").replace(" ", "").lower()
    sym = "KRW=X"   # 기본 = 원달러
    for keys, s in FX_ALIASES:
        if any(k.replace(" ", "").lower() in q for k in keys):
            sym = s
            break
    r = _yahoo(sym, "환율")
    if r.get("ok"):
        r["label"] = FX_SYMS.get(sym, ("환율", "", ""))
    return r


# ── 코인 (CoinGecko + Upbit) ─────────────────────────────────────
COIN_IDS = [
    (("비트코인", "비트", "bitcoin", "btc"), "bitcoin", "KRW-BTC"),
    (("이더리움", "이더", "ethereum", "eth"), "ethereum", "KRW-ETH"),
    (("리플", "xrp", "ripple"), "ripple", "KRW-XRP"),
    (("도지", "dogecoin", "doge"), "dogecoin", "KRW-DOGE"),
    (("솔라나", "solana", "sol"), "solana", "KRW-SOL"),
    (("에이다", "카르다노", "cardano", "ada"), "cardano", "KRW-ADA"),
    (("트론", "tron", "trx"), "tron", "KRW-TRX"),
]


def crypto(query):
    q = (query or "").replace(" ", "").lower()
    cid = mkt = name = None
    for keys, c, m in COIN_IDS:
        if any(k in q for k in keys):
            cid, mkt, name = c, m, keys[0]
            break
    if not cid:
        return {"ok": False, "msg": "어떤 코인인지 못 알아들었어(비트코인·이더리움·리플·도지·솔라나 등)."}
    try:
        cg = _get_json(f"https://api.coingecko.com/api/v3/simple/price?ids={cid}"
                       "&vs_currencies=usd,krw&include_24hr_change=true")
        d = cg.get(cid, {})
    except Exception as e:
        d = {}
        cg_err = str(e)
    out = {"ok": bool(d), "coin": name, "id": cid,
           "usd": d.get("usd"), "krw": d.get("krw"),
           "usd_24h_change": round(d["usd_24h_change"], 2)
               if d.get("usd_24h_change") is not None else None,
           "source": "CoinGecko"}
    # 업비트 국내 실거래가 보강(원화는 국내 거래소가 기준)
    if mkt:
        try:
            up = _get_json(f"https://api.upbit.com/v1/ticker?markets={mkt}")
            out["upbit_krw"] = up[0]["trade_price"]
            out["upbit_change_pct"] = round(up[0]["signed_change_rate"] * 100, 2)
            out["source"] = "CoinGecko + 업비트"
            out["ok"] = True
        except Exception:
            pass
    if not out["ok"]:
        return {"ok": False, "msg": f"코인 시세 조회 실패: {cg_err if not d else ''}"}
    return out


# ── 지수 (Yahoo) — 코스피·코스닥·나스닥·S&P·다우 등 ──────────────
#  지수는 '포인트'라 통화(KRW/USD)를 안 붙인다 → 전용 kind="지수".
INDEX_ALIASES = [
    (("코스피", "kospi", "ks11"), "^KS11", "코스피"),
    (("코스닥", "kosdaq", "kq11"), "^KQ11", "코스닥"),
    (("나스닥", "nasdaq", "ixic"), "^IXIC", "나스닥"),
    (("에스앤피", "s&p", "sp500", "s&p500", "gspc"), "^GSPC", "S&P 500"),
    (("다우존스", "다우", "dowjones", "dow", "dji"), "^DJI", "다우"),
    (("니케이", "nikkei", "n225"), "^N225", "니케이225"),
    (("항셍", "항생", "hsi"), "^HSI", "항셍"),
    (("변동성지수", "vix", "빅스", "공포지수아님"), "^VIX", "VIX(변동성)"),
]


def index(query):
    """지수 이름이 잡히면 Yahoo 지수 시세, 아니면 None."""
    q = (query or "").replace(" ", "").lower()
    for keys, sym, name in INDEX_ALIASES:
        if any(k.replace(" ", "").lower() in q for k in keys):
            r = _yahoo(sym, "지수")
            if r.get("ok"):
                r["index_name"] = name
            return r
    return None


# ── 주식 (Yahoo) — 지수 + 미국 티커/이름 + 한국 종목 이름/코드(.KS/.KQ) ──
import kr_stocks
import us_stocks


def _pick_kr_listing(code):
    """접미사 없는 한국 6자리 코드 → .KS/.KQ 중 거래량 있는 '진짜' listing.
       (한쪽은 거래량 없는 유령 펀드 listing이 잡힐 수 있어 vol로 가린다.)"""
    best = None
    for suf in (".KS", ".KQ"):
        sym = code + suf
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               "?interval=1d&range=1d")
        try:
            meta = _get_json(url)["chart"]["result"][0]["meta"]
        except Exception:
            continue
        if best is None:
            best = sym
        if meta.get("regularMarketVolume") is not None:
            return sym
    return best or code + ".KS"


def stock(ticker):
    t = (ticker or "").strip()
    if not t:
        return {"ok": False, "msg": "티커나 종목명이 필요해(예: AAPL, 삼성전자, 나스닥)."}
    # 지수(코스피·나스닥·S&P 등)면 지수로 직행
    idx = index(t)
    if idx is not None:
        return idx
    # 한국 종목 이름/코드면 .KS/.KQ 로 해석(공용 kr_stocks)
    kr = kr_stocks.name_to_ticker(t)
    if kr:
        if kr_stocks.is_bare_code(kr):
            kr = _pick_kr_listing(kr)
        return _yahoo(kr, "주가")
    # 미국 종목 한글/약칭 이름(엔비디아·테슬라·TQQQ 등) → 티커
    us = us_stocks.name_to_ticker(t)
    if us:
        return _yahoo(us, "주가")
    # 해외 티커 — 공백·한글 등 섞여 들어와도 티커 토큰만 뽑아 URL 깨짐 방지
    m = re.search(r"[A-Za-z][A-Za-z.\-]{0,6}", t)
    if not m:
        return {"ok": False, "msg": f"티커를 못 알아들었어: {t}"}
    return _yahoo(m.group(0).upper(), "주가")


# ── 공포·탐욕 지수 ──────────────────────────────────────────────
#  미장(주식) = CNN Fear&Greed(군중심리 7개 세부지표까지) — 기본.
#  코인       = alternative.me (Only Money 와 동일 소스).
_CNN_FG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_CNN_HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
    "Origin": "https://edition.cnn.com",
}
# CNN 세부지표 키 → 한국어 라벨 (군중심리 구성요소)
_CNN_COMPONENTS = {
    "market_momentum_sp500": "시장 모멘텀",
    "stock_price_strength": "주가 강도",
    "stock_price_breadth": "시장 폭(상승·하락)",
    "put_call_options": "풋/콜 옵션비율",
    "market_volatility_vix": "변동성(VIX)",
    "junk_bond_demand": "정크본드 수요",
    "safe_haven_demand": "안전자산 수요",
}


def stock_fear_greed():
    """CNN 미장 공포·탐욕(종합 + 군중심리 세부지표). 무키(브라우저 헤더 우회)."""
    try:
        req = urllib.request.Request(_CNN_FG_URL, headers=_CNN_HDR)
        with urllib.request.urlopen(req, timeout=12) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
        fg = j["fear_and_greed"]
        comps = []
        for k, lab in _CNN_COMPONENTS.items():
            v = j.get(k)
            if isinstance(v, dict) and v.get("score") is not None:
                comps.append({"name": lab, "score": round(v["score"], 1),
                              "rating": v.get("rating")})
        return {"ok": True, "kind": "공포탐욕", "market": "미국증시",
                "value": round(fg["score"]), "class": fg.get("rating"),
                "components": comps, "source": "CNN Fear & Greed"}
    except Exception as e:
        return {"ok": False, "msg": f"CNN 공포탐욕 조회 실패: {e}"}


def _crypto_fear_greed():
    try:
        j = _get_json("https://api.alternative.me/fng/?limit=1&format=json")
        d = j["data"][0]
        return {"ok": True, "kind": "공포탐욕", "market": "코인",
                "value": int(d["value"]), "class": d["value_classification"],
                "source": "alternative.me"}
    except Exception as e:
        return {"ok": False, "msg": f"공포탐욕지수 조회 실패: {e}"}


def fear_greed(query=""):
    """기본=미장(CNN). '코인/크립토/비트' 맥락이면 alternative.me."""
    q = (query or "").replace(" ", "").lower()
    if any(k in q for k in ("코인", "크립토", "비트", "암호화폐", "crypto", "btc", "이더")):
        return _crypto_fear_greed()
    return stock_fear_greed()


# ── 결과를 한국어 한 줄(들)로 — 숫자는 그대로, 모델 안 거침 ───────
def fmt(result):
    if not result or not result.get("ok"):
        return result.get("msg", "시세를 못 가져왔어.") if result else "시세를 못 가져왔어."
    k = result.get("kind")
    if k == "환율":
        name, unit, cur = result.get("label", ("환율", "", ""))
        line = f"{name}: {unit} = {result['price']:,.2f}{cur}"
        if result.get("change_pct") is not None:
            line += f" (전일대비 {result['change_pct']:+.2f}%)"
        return line + f" · {result['source']}"
    if k == "지수":
        line = f"{result.get('index_name', result['symbol'])}: {result['price']:,.2f}"
        if result.get("change_pct") is not None:
            line += f" ({result['change_pct']:+.2f}%)"
        return line + f" · {result['source']}"
    if k == "주가":
        line = f"{result['symbol']}: {result['price']:,.2f} {result.get('currency','')}"
        if result.get("change_pct") is not None:
            line += f" ({result['change_pct']:+.2f}%)"
        return line + f" · {result['source']}"
    if "coin" in result:
        parts = [f"{result['coin']}:"]
        if result.get("usd") is not None:
            parts.append(f"${result['usd']:,}")
        krw = result.get("upbit_krw") or result.get("krw")
        if krw is not None:
            parts.append(f"/ {krw:,.0f}원")
        chg = result.get("upbit_change_pct")
        if chg is None:
            chg = result.get("usd_24h_change")
        if chg is not None:
            parts.append(f"({chg:+.2f}%)")
        return " ".join(parts) + f" · {result['source']}"
    if "value" in result and "class" in result:
        mk = result.get("market", "")
        line = f"{mk} 공포·탐욕 지수: {result['value']} ({result['class']})".lstrip()
        comps = result.get("components") or []
        if comps:
            # 군중심리 세부 — 극단(extreme)은 다 보여주고, 없으면 핵심 3개
            ext = [c for c in comps if "extreme" in (c.get("rating") or "")]
            shown = ext if ext else comps[:3]
            line += "\n  · " + " · ".join(
                f"{c['name']} {c['score']}({c['rating']})" for c in shown)
        return line + f" · {result['source']}"
    return json.dumps(result, ensure_ascii=False)


# ── 미장 빠른 스냅샷 (모델 0 · 데이터만 · 즉시) ─────────────────
_SNAP_INDICES = [("^DJI", "다우"), ("^IXIC", "나스닥"), ("^GSPC", "S&P"), ("^SOX", "필반")]
_SNAP_MOVERS = [("NVDA", "엔비디아"), ("TSLA", "테슬라"), ("AAPL", "애플"),
                ("MSFT", "MS"), ("AMD", "AMD")]


def market_snapshot():
    """지수 + 군중심리(CNN) + 주요 종목 등락을 한 번에. 숫자는 Yahoo/CNN 실측(LLM 안 거침)."""
    idx = []
    for sym, name in _SNAP_INDICES:
        r = _yahoo(sym, "지수")
        if r.get("ok") and r.get("change_pct") is not None:
            idx.append(f"{name} {r['change_pct']:+.2f}%")
    mov = []
    for sym, name in _SNAP_MOVERS:
        r = _yahoo(sym, "주가")
        if r.get("ok") and r.get("change_pct") is not None:
            mov.append(f"{name} {r['change_pct']:+.1f}%")
    fg = stock_fear_greed()
    lines = ["📈 미장 스냅샷 (Yahoo·CNN 실측)"]
    if idx:
        lines.append("· 지수: " + " · ".join(idx))
    if fg.get("ok"):
        ext = [c["name"] for c in fg.get("components", []) if "extreme" in (c.get("rating") or "")]
        tail = f" — 극단 쏠림: {', '.join(ext)}" if ext else ""
        lines.append(f"· 군중심리(공포탐욕): {fg['value']} ({fg['class']}){tail}")
    if mov:
        lines.append("· 주요: " + " · ".join(mov))
    if len(lines) == 1:
        return {"ok": False, "msg": "미장 데이터 수집 실패 — 잠시 후 다시."}
    return {"ok": True, "kind": "market", "text": "\n".join(lines)}


def dispatch(action, args):
    if action == "get_market":
        return market_snapshot()
    if action == "get_fx":
        return fx(args.get("query", ""))
    if action == "get_crypto":
        return crypto(args.get("query", ""))
    if action == "get_stock":
        return stock(args.get("ticker") or args.get("query", ""))
    if action == "get_fear_greed":
        return fear_greed(args.get("query", ""))
    return {"ok": False, "msg": f"알 수 없는 시세 동작: {action}"}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("환율:", fmt(fx("원달러 환율")))
    print("엔화:", fmt(fx("엔화 환율")))
    print("비트코인:", fmt(crypto("비트코인 가격")))
    print("이더리움:", fmt(crypto("이더리움")))
    print("AAPL:", fmt(stock("AAPL")))
    print("공포탐욕:", fmt(fear_greed()))
