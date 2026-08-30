# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  미국/해외 종목·ETF 이름 → 티커 공용 (analyst·prices 단일 출처).
#   "엔비디아"·"테슬라"·"TQQQ" 같은 한글/약칭을 Yahoo 티커로.
#   의존성 0. (한국 종목은 kr_stocks, 지수는 prices.INDEX_ALIASES 가 따로 담당.)
# ═══════════════════════════════════════════════════════════════

# 자주 보는 이름 → 미국 티커 (없으면 웹검색이 커버, 가격 그라운딩만 생략)
NAME2TICKER = {
    "엔비디아": "NVDA", "엔비댜": "NVDA", "nvidia": "NVDA",
    "테슬라": "TSLA", "tesla": "TSLA",
    "애플": "AAPL", "apple": "AAPL",
    "마이크로소프트": "MSFT", "ms": "MSFT", "microsoft": "MSFT",
    "아마존": "AMZN", "구글": "GOOGL", "알파벳": "GOOGL", "메타": "META",
    "amd": "AMD", "브로드컴": "AVGO", "마이크론": "MU", "퀄컴": "QCOM",
    "tsmc": "TSM", "팔란티어": "PLTR", "팔란": "PLTR",
    "넷플릭스": "NFLX", "코인베이스": "COIN", "마이크로스트래티지": "MSTR",
    # ETF / 레버리지
    "tqqq": "TQQQ", "sqqq": "SQQQ", "soxl": "SOXL", "soxs": "SOXS",
    "qqq": "QQQ", "spy": "SPY", "voo": "VOO", "nvdl": "NVDL", "tsll": "TSLL",
    # 우주·방산(미국 상장)
    "우주산업": "ARKX", "우주": "ARKX", "arkx": "ARKX", "로켓랩": "RKLB",
    "rklb": "RKLB", "인튜이티브머신": "LUNR", "lunr": "LUNR", "asts": "ASTS",
    "방산": "ITA", "ita": "ITA", "록히드": "LMT", "lmt": "LMT", "rtx": "RTX",
}


def name_to_ticker(query):
    """문장에서 미국 종목 이름이 잡히면 티커, 없으면 None."""
    ql = (query or "").lower()
    for name, tk in NAME2TICKER.items():
        if name in ql:
            return tk
    return None
