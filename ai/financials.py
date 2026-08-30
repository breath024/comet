# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 재무·실적 엔진 (financials)
#   Yahoo quoteSummary(무키, crumb+쿠키 우회) → 매출성장·마진·밸류에이션·
#   애널리스트 목표가·어닝 서프라이즈·매출 추세. 숫자는 API가 준 그대로(LLM 안 거침=날조 불가).
#   prices.py 와 같은 철학. 그래프는 의존성0 ASCII 스파크라인.
#   ⚠️ 미국/해외 티커 위주(Yahoo). 한국 공시(DART)는 키 필요 → 별도.
# ═══════════════════════════════════════════════════════════════
import json
import urllib.request
import urllib.parse
import http.cookiejar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

_OP = None
_CRUMB = None


def _session():
    """Yahoo 쿠키+crumb 1회 확보 후 캐시(quoteSummary가 crumb 요구)."""
    global _OP, _CRUMB
    if _OP and _CRUMB:
        return _OP, _CRUMB
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    try:
        op.open("https://fc.yahoo.com", timeout=10).read()
    except Exception:
        pass
    crumb = op.open("https://query1.finance.yahoo.com/v1/test/getcrumb",
                    timeout=10).read().decode("utf-8", "ignore").strip()
    _OP, _CRUMB = op, crumb
    return op, crumb


def _qs(ticker, modules):
    op, crumb = _session()
    u = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
         f"?modules={modules}&crumb=" + urllib.parse.quote(crumb))
    j = json.loads(op.open(u, timeout=12).read().decode("utf-8", "ignore"))
    res = (j.get("quoteSummary") or {}).get("result")
    return res[0] if res else None


_BARS = "▁▂▃▄▅▆▇█"


def _spark(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return _BARS[0] * len(vals)
    return "".join(_BARS[int((v - lo) / (hi - lo) * (len(_BARS) - 1))] for v in vals)


def _f(d, k):
    return (d.get(k) or {}).get("fmt")


def _raw(d, k):
    return (d.get(k) or {}).get("raw")


def fundamentals(ticker):
    t = (ticker or "").strip().upper()
    if not t:
        return {"ok": False, "msg": "티커 필요"}
    try:
        r = _qs(t, "financialData,defaultKeyStatistics,summaryDetail,earnings,earningsHistory")
    except Exception as e:
        return {"ok": False, "msg": f"재무 조회 실패: {e}"}
    if not r:
        return {"ok": False, "msg": f"{t} 재무 데이터 없음"}
    fd = r.get("financialData", {})
    ks = r.get("defaultKeyStatistics", {})
    sd = r.get("summaryDetail", {})
    ea = r.get("earnings", {})
    out = {
        "ok": True, "ticker": t, "source": "Yahoo Finance (무키)",
        "rev_growth": _f(fd, "revenueGrowth"), "earn_growth": _f(fd, "earningsGrowth"),
        "gross": _f(fd, "grossMargins"), "oper": _f(fd, "operatingMargins"),
        "profit": _f(fd, "profitMargins"), "roe": _f(fd, "returnOnEquity"),
        "debt_eq": _f(fd, "debtToEquity"), "cash": _f(fd, "totalCash"),
        "target_mean": _f(fd, "targetMeanPrice"), "target_high": _f(fd, "targetHighPrice"),
        "target_low": _f(fd, "targetLowPrice"), "recommend": fd.get("recommendationKey"),
        "n_analysts": _raw(fd, "numberOfAnalystOpinions"),
        "fwd_pe": _f(ks, "forwardPE") or _f(sd, "forwardPE"),
        "trail_pe": _f(sd, "trailingPE"), "peg": _f(ks, "pegRatio"),
        "pb": _f(ks, "priceToBook"),
    }
    # 매출 추세(연/분기) — 그래프용
    fc = ea.get("financialsChart", {})
    out["rev_yearly"] = [((y.get("date")), _raw(y, "revenue")) for y in fc.get("yearly", [])]
    out["rev_quarterly"] = [((q.get("date")), _raw(q, "revenue")) for q in fc.get("quarterly", [])]
    # 다음 실적일
    ec = ea.get("earningsChart", {})
    ed = ec.get("earningsDate") or []
    out["next_earnings"] = ed[0].get("fmt") if ed and isinstance(ed[0], dict) else None
    # 어닝 서프라이즈(최근 분기들)
    eh = r.get("earningsHistory", {}).get("history", [])
    out["surprises"] = [((h.get("quarter") or {}).get("fmt"), _f(h, "surprisePercent"))
                        for h in eh]
    return out


def _bil(v):
    try:
        return f"${v/1e9:,.1f}B"
    except Exception:
        return "?"


def fmt(f):
    """펀더멘털 → 한국어 텍스트 블록 + ASCII 매출 추세 그래프. (실데이터, 날조 아님)"""
    if not f or not f.get("ok"):
        return f.get("msg", "재무 데이터 없음") if f else "재무 데이터 없음"
    L = [f"📊 펀더멘털 — {f['ticker']} (Yahoo, 무키 실데이터)"]
    g = []
    if f.get("rev_growth"):
        g.append(f"매출성장 YoY {f['rev_growth']}")
    if f.get("earn_growth"):
        g.append(f"순익성장 {f['earn_growth']}")
    if f.get("profit"):
        g.append(f"순이익률 {f['profit']}")
    if f.get("gross"):
        g.append(f"매출총이익률 {f['gross']}")
    if g:
        L.append("- 성장·마진: " + " · ".join(g))
    val = []
    for k, lab in (("fwd_pe", "선행PER"), ("trail_pe", "PER"), ("peg", "PEG"), ("pb", "PBR")):
        if f.get(k):
            val.append(f"{lab} {f[k]}")
    if f.get("roe"):
        val.append(f"ROE {f['roe']}")
    if val:
        L.append("- 밸류에이션: " + " · ".join(val))
    if f.get("target_mean"):
        tline = f"- 애널리스트 목표가: 평균 {f['target_mean']}"
        if f.get("target_low") and f.get("target_high"):
            tline += f" (저 {f['target_low']} ~ 고 {f['target_high']})"
        if f.get("recommend"):
            tline += f" · 의견 {f['recommend']}"
        if f.get("n_analysts"):
            tline += f" · {f['n_analysts']}명"
        L.append(tline)
    # 매출 추세 그래프(연간)
    yr = [v for _, v in f.get("rev_yearly", []) if v]
    if len(yr) >= 2:
        yrs = [str(d) for d, v in f.get("rev_yearly", []) if v]
        L.append(f"- 매출 추세(연): {_spark(yr)}  {yrs[0]}→{yrs[-1]} (최신 {_bil(yr[-1])})")
    sp = [s for s in f.get("surprises", []) if s[1]]
    if sp:
        L.append("- 어닝 서프라이즈(최근): " + " · ".join(f"{q} {pct}" for q, pct in sp[:4]))
    if f.get("next_earnings"):
        L.append(f"- 다음 실적발표: {f['next_earnings']}")
    return "\n".join(L)


def dispatch(action, args):
    if action == "get_financials":
        return fundamentals(args.get("ticker") or args.get("query", ""))
    return {"ok": False, "msg": f"알 수 없는 재무 동작: {action}"}


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    t = (sys.argv[1] if len(sys.argv) > 1 else "NVDA")
    print(fmt(fundamentals(t)))
