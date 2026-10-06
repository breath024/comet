# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 알림 통로 — 텔레그램 (표준 라이브러리만, 의존성 0)
#   감시 엔진(news.py)이 고른 신규/긴급 기사를 폰으로 푸시한다.
#   설정: telegram_config.json = {"token":"<봇토큰>", "chat_id":"<내 chat id>"}
#   토큰 발급: 텔레그램에서 @BotFather → /newbot → 받은 토큰.
#   chat_id: 봇과 대화 한 번 건 뒤 get_chat_id()로 자동 조회.
# ═══════════════════════════════════════════════════════════════
import os
import re
import html
import json
import urllib.error
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


def _plain(text):
    """HTML 태그를 벗긴 맨글 — 텔레그램이 HTML 을 거절했을 때 다시 보낼 글."""
    return html.unescape(re.sub(r"<[^>]+>", "", text or ""))


def send(text: str, html_mode=True) -> dict:
    cfg = _load_cfg()
    token, cid = cfg.get("token"), cfg.get("chat_id")
    if not (token and cid):
        return {"ok": False, "msg": "텔레그램 미설정 (token/chat_id 필요)"}
    data = {"chat_id": cid, "text": text[:4000], "disable_web_page_preview": "true"}
    if html_mode:
        data["parse_mode"] = "HTML"
    try:
        res = _call("sendMessage", token, data)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        # 태그가 깨진 글(모델이 쓴 '<', 4000자에서 잘린 태그)은 400 "can't parse entities" 로
        # 통째로 거절된다 — 그대로 두면 같은 글로 재시도만 돈다. 태그를 벗겨 한 번 더 보낸다.
        if html_mode and "parse entities" in body:
            r = send(_plain(text), html_mode=False)
            if r.get("ok"):
                r["msg"] = "전송됨(HTML 거절 → 맨글로)"      # 로그에서 모양이 깨졌다는 걸 알 수 있게
            return r
        return {"ok": False, "msg": f"전송 실패: {e} {body[:200]}"}
    except Exception as e:
        return {"ok": False, "msg": f"전송 실패: {e}"}
    return {"ok": bool(res.get("ok")), "msg": "전송됨" if res.get("ok") else str(res)}


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── 받기 — 텔레그램으로 물으면 답하기(autonomy.TelegramMode, 2026-10-07) ─────────
#  원래 이 파일은 보내기만 했다 — 받는 건 처음 연결할 때 chat_id 찾기(get_chat_id) 한 번뿐.
def chat_id() -> str:
    return str(_load_cfg().get("chat_id") or "")


def updates(offset=None, timeout=25):
    """새로 온 말 목록(getUpdates 길게 기다리기). timeout 초 동안 새 말이 없으면 빈 목록.
       offset = 마지막으로 처리한 update_id + 1 — 그 앞은 텔레그램이 '처리 끝'으로 보고 다시 안 준다.
       같은 봇 토큰을 두 곳이 동시에 기다리면 409(Conflict) — HTTPError 그대로 올린다."""
    cfg = _load_cfg()
    data = {"timeout": int(timeout), "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        data["offset"] = int(offset)
    req = urllib.request.Request(API.format(token=cfg.get("token"), method="getUpdates"),
                                 data=urllib.parse.urlencode(data).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=timeout + 15) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("ok"):
        raise RuntimeError(str(res)[:200])
    return res.get("result") or []


def typing():
    """대화방에 '입력 중…' (5초 동안 보인다 — 오래 생각하면 계속 다시 보낸다). 실패해도 조용히."""
    cfg = _load_cfg()
    try:
        _call("sendChatAction", cfg.get("token"), {"chat_id": cfg.get("chat_id"), "action": "typing"})
    except Exception:
        pass


def md_to_tg(md):
    """코멧 답(마크다운 섞인 글) → 텔레그램 HTML. **굵게**·### 제목·`코드`·```블록```·'- ' 목록만.
       나머지는 전부 이스케이프 — 이상한 글이 와도 send() 가 거절되면 맨글로 다시 보낸다."""
    out, code = [], False
    for line in (md or "").split("\n"):
        if line.strip().startswith("```"):
            out.append("</pre>" if code else "<pre>")
            code = not code
            continue
        if code:
            out.append(_esc(line))
            continue
        h = re.match(r"\s*#{1,6}\s+(.*)", line)
        if h:                                            # 제목 = 한 줄 통째 굵게(안의 ** 는 떼야 태그가 안 겹친다)
            out.append(f"<b>{_esc(h.group(1).replace('**', ''))}</b>")
            continue
        s = _esc(re.sub(r"^(\s*)[-*]\s+", r"\1• ", line))
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
        out.append(s)
    if code:
        out.append("</pre>")
    return "\n".join(out)


def send_long(md, limit=3500):
    """긴 답을 문단 경계에서 나눠 차례로 보낸다(한 통 4096자 한도). 나눈 뒤에 HTML 로 바꿔 태그가 안 잘리게."""
    chunks, cur = [], ""
    for para in (md or "").split("\n\n"):
        while len(para) > limit:                       # 한 문단이 통째로 긴 경우 — 줄 경계에서
            cut = para.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:cut])
            para = para[cut:].lstrip("\n")
        if cur and len(cur) + len(para) + 2 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur.strip():
        chunks.append(cur)
    res = [send(md_to_tg(c)) for c in chunks if c.strip()]
    return {"ok": bool(res) and all(r.get("ok") for r in res), "parts": len(res),
            "msg": " / ".join(r.get("msg", "") for r in res)}


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
