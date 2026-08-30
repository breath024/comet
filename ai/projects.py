# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 프로젝트 데이터 연결 (읽기 전용)
#   호윤의 코인봇/주식봇 거래 CSV를 읽어 통계를 "파이썬이" 계산한다.
#   숫자는 코드가 계산하고 모델은 결과를 말로만 옮긴다(어림셈 금지).
#   실거래 / 백테스트 / 모의 를 명확히 구분해서 표기.
# ═══════════════════════════════════════════════════════════════
import os
import csv

COIN  = r"C:\Users\USER\Desktop\창업\코인봇"
STOCK = r"C:\Users\USER\Desktop\창업\주식봇"

# 데이터 소스 등록 — schema 별로 계산법이 다름
SOURCES = {
    "코인봇_실거래":   {"path": os.path.join(COIN, "real_trades_100x.csv"),
                      "schema": "event", "pnl": "실현손익", "entry": "종류",
                      "unit": "USDT", "kind": "실거래"},
    "코인봇_백테스트": {"path": os.path.join(COIN, "crypto_trades.csv"),
                      "schema": "event", "pnl": "실현손익", "entry": "종류",
                      "unit": "USDT", "kind": "백테스트"},
    "코인봇_매매기록": {"path": os.path.join(COIN, "매매기록.csv"),
                      "schema": "trade", "pnl": "PNL_USDT",
                      "unit": "USDT", "kind": "수동기록"},
    "코인봇_모의":     {"path": os.path.join(COIN, "paper_trades_log.csv"),
                      "schema": "roi", "roi": "실현ROI%",
                      "unit": "%", "kind": "모의투자"},
    "주식봇_백테스트": {"path": os.path.join(STOCK, "backtest_trades.csv"),
                      "schema": "stock", "unit": "원", "kind": "백테스트"},
}

# 사용자 표현 → 소스 키 (앞에서부터 먼저 맞는 것)
def _resolve_source(q: str) -> str:
    s = (q or "").lower().replace(" ", "")
    coin = "코인" in s or "비트" in s or "btc" in s or "선물" in s
    stock = "주식" in s or "주가" in s or "종목" in s
    if stock and not coin:
        return "주식봇_백테스트"
    # 코인 계열 세부
    if "모의" in s or "paper" in s:
        return "코인봇_모의"
    if "백테" in s or "시뮬" in s or "테스트" in s:
        return "코인봇_백테스트"
    if "매매기록" in s:
        return "코인봇_매매기록"
    return "코인봇_실거래"      # 기본 = 실거래


def _f(x):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return 0.0


def _read(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8-sig", newline="") as fp:
        return list(csv.DictReader(fp))


# ── 스키마별 통계 계산 ───────────────────────────────────────
def _stats_event(rows, cfg):
    closed = [r for r in rows if (r.get(cfg["entry"], "") or "").upper() != "ENTRY"]
    pnls = [_f(r.get(cfg["pnl"])) for r in closed]
    return _summarize(pnls, cfg, recent_rows=closed)


def _stats_trade(rows, cfg):
    pnls = [_f(r.get(cfg["pnl"])) for r in rows]
    return _summarize(pnls, cfg, recent_rows=rows)


def _stats_roi(rows, cfg):
    pnls = [_f(r.get(cfg["roi"])) for r in rows]
    return _summarize(pnls, cfg, recent_rows=rows)


def _stats_stock(rows, cfg):
    # BUY/SELL FIFO 페어링으로 실현손익 계산
    lots, realized = {}, []
    for r in rows:
        sym = r.get("종목", "")
        side = (r.get("방향", "") or "").upper()
        qty, price = _f(r.get("수량")), _f(r.get("가격"))
        fee, tax = _f(r.get("수수료")), _f(r.get("세금"))
        if side == "BUY":
            lots.setdefault(sym, []).append([qty, price, fee])
        elif side == "SELL":
            q = qty
            buy_cost = buy_fee = 0.0
            while q > 0 and lots.get(sym):
                lot = lots[sym][0]
                take = min(q, lot[0])
                buy_cost += take * lot[1]
                buy_fee  += lot[2] * (take / lot[0]) if lot[0] else 0
                lot[0] -= take
                q -= take
                if lot[0] <= 0:
                    lots[sym].pop(0)
            pnl = (price * qty - fee - tax) - (buy_cost + buy_fee)
            realized.append(pnl)
    return _summarize(realized, cfg, recent_rows=rows)


def _summarize(pnls, cfg, recent_rows):
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    out = {
        "ok": True, "kind": cfg["kind"], "unit": cfg["unit"],
        "거래수": n,
        "승": wins, "패": n - wins,
        "승률%": round(wins / n * 100, 1) if n else 0.0,
        "총손익": round(total, 2),
        "평균손익": round(total / n, 2) if n else 0.0,
    }
    # 최근 3건 요약 (행 끝쪽)
    tail = recent_rows[-3:]
    out["최근"] = [{k: r.get(k) for k in list(r.keys())[:6]} for r in tail]
    return out


_SCHEMA = {"event": _stats_event, "trade": _stats_trade,
           "roi": _stats_roi, "stock": _stats_stock}


# ── 공개 도구 ────────────────────────────────────────────────
def list_sources() -> dict:
    items = []
    for key, cfg in SOURCES.items():
        rows = _read(cfg["path"])
        items.append({"소스": key, "종류": cfg["kind"],
                      "행수": (len(rows) if rows else 0),
                      "있음": rows is not None})
    return {"ok": True, "items": items}


def trade_stats(source: str = "") -> dict:
    key = _resolve_source(source)
    cfg = SOURCES.get(key)
    if not cfg:
        return {"ok": False, "msg": f"알 수 없는 소스: {source}"}
    rows = _read(cfg["path"])
    if rows is None:
        return {"ok": False, "msg": f"{key} 데이터 파일 없음 ({cfg['path']})"}
    if not rows:
        return {"ok": False, "msg": f"{key}에 거래 기록이 없음"}
    result = _SCHEMA[cfg["schema"]](rows, cfg)
    result["소스"] = key
    return result


ACTIONS = {"trade_stats", "list_sources"}
_DISPATCH = {"trade_stats": trade_stats, "list_sources": list_sources}


def dispatch(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"ok": False, "msg": f"알 수 없는 도구: {name}"}
    try:
        return fn(**(args or {}))
    except TypeError as e:
        return {"ok": False, "msg": f"인자 오류: {e}"}
    except Exception as e:
        return {"ok": False, "msg": f"실행 오류: {e}"}
