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
import time
import urllib.error
import urllib.parse
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(_DIR, "telegram_config.json")
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15            # 초 — 보내기(sendMessage 등) 한 번의 urlopen 타임아웃. 답 시간 로그에도 찍힌다


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
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def get_chat_id(force: bool = False) -> dict:
    """봇과 한 번 대화한 뒤 호출 → 최근 메시지에서 chat_id를 찾아 저장."""
    cfg = _load_cfg()
    token = cfg.get("token")
    if not token:
        return {"ok": False, "msg": "telegram_config.json에 token부터 넣어줘 (@BotFather 발급)"}
    # 이미 있으면 덮어쓰지 않는다 — 마지막으로 말 건 사람을 주인으로 저장해서, 남이 /start 를 보내 두면
    #   그 방이 「호윤 대화방」이 됐다(10/10 T-18). 바꾸려면 `python notify.py --reset`.
    if cfg.get("chat_id") and not force:
        return {"ok": True, "msg": "chat_id 이미 있음 — 바꾸려면 python notify.py --reset"}
    try:
        res = _call("getUpdates", token, {})
    except Exception as e:
        return {"ok": False, "msg": f"텔레그램 연결 실패(토큰 확인): {e}"}
    updates = res.get("result", [])
    if not updates:
        return {"ok": False, "msg": "봇에게 아무 메시지나 한 번 보낸 뒤 다시 실행해줘 (예: '안녕')"}
    chats = {}
    for u in updates:
        c = (u.get("message") or {}).get("chat") or {}
        if c.get("id"):
            chats[c["id"]] = c
    if len(chats) != 1:                     # 여러 대화방이 말을 걸어 뒀으면 고르지 않는다(남이 끼어든 것일 수 있다)
        return {"ok": False, "msg": "봇에게 말 건 대화방이 %d개야 — 다른 사람 글이 섞였을 수 있어서 저장 안 함." % len(chats)}
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


def _log_try(part, k, ok, sec):
    """보내기 시도 하나를 끝난 그 자리에서 콘솔에 — 지연 안내처럼 send() 를 바로 부른 것도 남게(10/14)."""
    try:
        print(f"[텔레그램] 보내기{part} {k}번째 {'성공' if ok else '실패'} {sec:.1f}s"
              f"(urlopen 타임아웃 {TIMEOUT}초)", flush=True)
    except Exception:
        pass


def send(text: str, html_mode=True, part="", _try=1, record=True) -> dict:
    #  record — 보낸 글을 outbox 에 남긴다(대화 밖에서 나간 걸 코멧이 알게 · 10/16). 대화 답은 이미 기록에 있어 False
    cfg = _load_cfg()
    token, cid = cfg.get("token"), cfg.get("chat_id")
    if not (token and cid):
        return {"ok": False, "msg": "텔레그램 미설정 (token/chat_id 필요)"}
    data = {"chat_id": cid, "text": text[:4000], "disable_web_page_preview": "true"}
    if html_mode:
        data["parse_mode"] = "HTML"
    # tries = 시도마다 (성공?, 걸린 초) — 텔레그램 답이 늦는 원인 찾기용(10/14). 동작은 그대로
    #   part = 로그에 붙일 꼬리(send_long 의 " 조각 2/3 …자"), _try = 몇 번째 시도(맨글 다시 보내기면 2)
    t0 = time.time()
    try:
        res = _call("sendMessage", token, data)
    except urllib.error.HTTPError as e:
        tries = [(False, time.time() - t0)]
        _log_try(part, _try, *tries[0])
        body = e.read().decode("utf-8", "ignore")
        # 태그가 깨진 글(모델이 쓴 '<', 4000자에서 잘린 태그)은 400 "can't parse entities" 로
        # 통째로 거절된다 — 그대로 두면 같은 글로 재시도만 돈다. 태그를 벗겨 한 번 더 보낸다.
        if html_mode and "parse entities" in body:
            r = send(_plain(text), html_mode=False, part=part, _try=_try + 1, record=record)
            if r.get("ok"):
                r["msg"] = "전송됨(HTML 거절 → 맨글로)"      # 로그에서 모양이 깨졌다는 걸 알 수 있게
            r["tries"] = tries + r.get("tries", [])
            return r
        return {"ok": False, "msg": f"전송 실패: {e} {body[:200]}", "tries": tries}
    except Exception as e:
        tries = [(False, time.time() - t0)]
        _log_try(part, _try, *tries[0])
        return {"ok": False, "msg": f"전송 실패: {e}", "tries": tries}
    tries = [(bool(res.get("ok")), time.time() - t0)]
    _log_try(part, _try, *tries[0])
    if record and res.get("ok"):
        import outbox
        outbox.record("텔레그램", _plain(text))
    return {"ok": bool(res.get("ok")), "msg": "전송됨" if res.get("ok") else str(res), "tries": tries}


PHOTO_EXT = (".png", ".jpg", ".jpeg", ".webp")


def send_file(path: str, caption: str = "") -> dict:
    """파일 · 사진을 텔레그램으로 (2026-10-16). 예전엔 글만 보낼 수 있어서 「로고 보내줘」에
       글자 「텔레그램으로 보냈어」만 가고 보냈다고 답했다. 사진(10MB 미만)은 sendPhoto, 그 밖은 sendDocument."""
    cfg = _load_cfg()
    token, cid = cfg.get("token"), cfg.get("chat_id")
    if not (token and cid):
        return {"ok": False, "msg": "텔레그램 미설정 (token/chat_id 필요)"}
    if not os.path.isfile(path):
        return {"ok": False, "msg": f"파일이 없어: {path}"}
    size = os.path.getsize(path)
    if size > 50 * 1024 * 1024:
        return {"ok": False, "msg": f"50MB 넘어서 텔레그램으로 못 보내: {os.path.basename(path)}"}
    photo = path.lower().endswith(PHOTO_EXT) and size < 10 * 1024 * 1024
    method, field = ("sendPhoto", "photo") if photo else ("sendDocument", "document")
    boundary = "comet" + os.urandom(8).hex()
    name = os.path.basename(path)
    parts = []
    for k, v in (("chat_id", cid), ("caption", (caption or "")[:1000])):
        if v:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8"))
    with open(path, "rb") as f:
        blob = f.read()
    #  ★filename*=UTF-8''… (RFC 5987) 로 주면 텔레그램이 파일로 못 알아보고 글(URL)로 읽어 400 — 10/16 실측. 이름 그대로 넣는다
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; '
            f'filename="{name.replace(chr(34), "")}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode("utf-8")
    body = b"".join(parts) + head + blob + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(API.format(token=token, method=method), data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "msg": f"파일 전송 실패: {e} {e.read().decode('utf-8', 'ignore')[:200]}"}
    except Exception as e:
        return {"ok": False, "msg": f"파일 전송 실패: {e}"}
    print(f"[텔레그램] 파일 {name} {'성공' if res.get('ok') else '실패'} {time.time() - t0:.1f}s", flush=True)
    if res.get("ok"):
        import outbox
        outbox.record("텔레그램 파일", f"{name} ({path})" + (f" — {caption}" if caption else ""))
    return {"ok": bool(res.get("ok")), "msg": f"텔레그램으로 {name} 보냈어." if res.get("ok") else str(res)[:200]}


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


def send_long(md, limit=3500, record=True):
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
    chunks = [c for c in chunks if c.strip()]
    res, times = [], []                                # times = 조각마다 (글자 수, 걸린 초, 시도 목록)
    for i, c in enumerate(chunks, 1):
        t0 = time.time()
        r = send(md_to_tg(c), part=f" 조각 {i}/{len(chunks)} {len(c)}자", record=record)
        res.append(r)
        times.append((len(c), time.time() - t0, r.get("tries", [])))
    return {"ok": bool(res) and all(r.get("ok") for r in res), "parts": len(res),
            "msg": " / ".join(r.get("msg", "") for r in res), "times": times}


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
    print(get_chat_id(force="--reset" in sys.argv) if configured() or _load_cfg().get("token") else
          "telegram_config.json에 {\"token\":\"...\"} 먼저 넣어줘")
