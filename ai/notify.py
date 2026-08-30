# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 알림 통로 — 텔레그램 (표준 라이브러리만, 의존성 0)
#   감시 엔진(news.py)이 고른 신규/긴급 기사를 폰으로 푸시한다.
#   설정: telegram_config.json = {"token":"<봇토큰>", "chat_id":"<내 chat id>"}
#   토큰 발급: 텔레그램에서 @BotFather → /newbot → 받은 토큰.
#   chat_id: 봇과 대화 한 번 건 뒤 get_chat_id()로 자동 조회.
# ═══════════════════════════════════════════════════════════════
import os
import json
import urllib.parse
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_DIR, "telegram_config.json")
API = "https://api.telegram.org/bot{token}/{method}"


def _load_cfg():
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(cfg):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def configured() -> bool:
    c = _load_cfg()
    return bool(c.get("token") and c.get("chat_id"))


def _call(method, token, data):
    url = API.format(token=token, method=method)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def get_chat_id() -> dict:
    """봇과 한 번 대화한 뒤 호출 → 최근 메시지에서 chat_id를 찾아 저장."""
    cfg = _load_cfg()
    token = cfg.get("token")
    if not token:
        return {"ok": False, "msg": "telegram_config.json에 token부터 넣어줘 (@BotFather 발급)"}
    try:
        res = _call("getUpdates", token, {})
    except Exception as e:
        return {"ok": False, "msg": f"텔레그램 연결 실패(토큰 확인): {e}"}
    updates = res.get("result", [])
    if not updates:
        return {"ok": False, "msg": "봇에게 아무 메시지나 한 번 보낸 뒤 다시 실행해줘 (예: '안녕')"}
    chat = updates[-1].get("message", {}).get("chat", {})
    cid = chat.get("id")
    if not cid:
        return {"ok": False, "msg": "chat_id를 못 찾음. 봇에게 메시지 보내고 재시도."}
    cfg["chat_id"] = str(cid)
    _save_cfg(cfg)
    return {"ok": True, "msg": f"chat_id 저장됨: {cid} ({chat.get('first_name','')})"}


def send(text: str) -> dict:
    cfg = _load_cfg()
    token, cid = cfg.get("token"), cfg.get("chat_id")
    if not (token and cid):
        return {"ok": False, "msg": "텔레그램 미설정 (token/chat_id 필요)"}
    try:
        res = _call("sendMessage", token, {
            "chat_id": cid, "text": text[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        })
    except Exception as e:
        return {"ok": False, "msg": f"전송 실패: {e}"}
    return {"ok": bool(res.get("ok")), "msg": "전송됨" if res.get("ok") else str(res)}


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_digest(new_items, urgent_items, max_general=8) -> str:
    """신규/긴급 기사를 텔레그램용으로 정리. 긴급(법정·제재) 먼저, 종목별 묶음."""
    lines = []
    if urgent_items:
        lines.append("🚨 <b>주의 이벤트</b>")
        for it in urgent_items[:12]:
            tag = _esc(it.get("trigger", ""))
            lines.append(f'· [{_esc(it["keyword"])}/{tag}] <a href="{_esc(it["link"])}">{_esc(it["title"])}</a> — {_esc(it["source"])}')
        lines.append("")
    general = [x for x in new_items if not x.get("urgent")]
    if general:
        lines.append("📰 <b>새 소식</b>")
        by_kw = {}
        for it in general:
            by_kw.setdefault(it["keyword"], []).append(it)
        shown = 0
        for kw, items in by_kw.items():
            lines.append(f"<b>{_esc(kw)}</b>")
            for it in items:
                if shown >= max_general:
                    break
                lines.append(f'· <a href="{_esc(it["link"])}">{_esc(it["title"])}</a> — {_esc(it["source"])}')
                shown += 1
            if shown >= max_general:
                break
    if not lines:
        return ""
    return "\n".join(lines).strip()


if __name__ == "__main__":
    import sys, io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    print("configured:", configured())
    print(get_chat_id() if configured() or _load_cfg().get("token") else
          "telegram_config.json에 {\"token\":\"...\"} 먼저 넣어줘")
