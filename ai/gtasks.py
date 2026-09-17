# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  구글 할 일(Google Tasks) → COMET 할일 가져오기 (읽기 전용, 한 방향)
#
#   할일 가져오기        → 구글 할 일을 COMET 으로 (중복 없이 갱신)
#   할일 구글연결        → 최초 1회 인증(브라우저 한 번)
#
#  ★왜 캘린더 ICS 처럼 간단히 못 하나: 구글은 Calendar 와 Tasks 가 **별도 시스템**이라
#    캘린더 ICS 피드에 할 일이 안 실린다. Tasks 는 OAuth 로만 읽힌다.
#  ★한계(문서 원문): "Only date information is recorded; the time portion of the timestamp
#    is discarded" — 구글 할 일에는 **시각이 없다.** 날짜 마감만 온다.
#  ★OAuth 동의화면이 'Testing' 이면 refresh token 이 7일마다 만료된다 → 상주 데몬이
#    조용히 끊긴다. 반드시 'Production(프로덕션)' 으로 게시할 것.
#
#  SDK 안 쓴다(urllib 만) — cloud.py 와 같은 방식. pip 설치 불필요.
#  비밀: gtasks_config.json (client_id/secret/refresh_token) — .gitignore 에 있음.
# ═══════════════════════════════════════════════════════════════
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(_DIR, "gtasks_config.json")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://tasks.googleapis.com/tasks/v1"
SCOPE = "https://www.googleapis.com/auth/tasks.readonly"

_token_cache = {"access": None, "exp": 0}


# ═══════════════════════════════════════════════════════════════
#  설정
# ═══════════════════════════════════════════════════════════════
def _load():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def configured():
    c = _load()
    return bool(c.get("client_id") and c.get("client_secret"))


def linked():
    return bool(_load().get("refresh_token"))


def status():
    c = _load()
    if not configured():
        return ("구글 할 일: 아직 준비 안 됨.\n"
                "1) console.cloud.google.com 에서 프로젝트 만들고 'Tasks API' 사용 설정\n"
                "2) OAuth 동의화면 = 외부 + **게시(프로덕션)** ← Testing 이면 7일마다 끊김\n"
                "3) 사용자 인증 정보 → OAuth 클라이언트 ID → **데스크톱 앱**\n"
                "4) 나온 클라이언트 ID/보안비밀을 gtasks_config.json 에 넣기\n"
                '   {"client_id": "...", "client_secret": "..."}\n'
                "5) COMET 에 '할일 구글연결' → 브라우저에서 허용 한 번")
    if not linked():
        return "구글 할 일: 키는 있는데 인증 전. '할일 구글연결' 한 번 하면 돼."
    last = c.get("last_sync") or "아직"
    return f"구글 할 일: 연결됨. 마지막 가져오기 {last}. ('할일 가져오기')"


# ═══════════════════════════════════════════════════════════════
#  OAuth — 최초 1회 (로컬 루프백)
# ═══════════════════════════════════════════════════════════════
def authorize(open_browser=True, timeout=180, log=print):
    """브라우저에서 허용 한 번 → refresh_token 저장. 콘솔에서 실행하는 용도."""
    cfg = _load()
    if not configured():
        return {"ok": False, "msg": "client_id/secret 부터 gtasks_config.json 에 넣어줘."}

    import http.server
    import socket
    import threading
    import webbrowser

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    redirect = f"http://127.0.0.1:{port}"
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in got
            self.wfile.write(("<h3>" + ("됐어. 창 닫고 COMET 으로 돌아가." if ok
                                        else "실패: " + got.get("error", "?")) + "</h3>")
                             .encode("utf-8"))

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
    log("브라우저에서 구글 계정 허용해줘:\n" + url)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    deadline = time.time() + timeout
    while "code" not in got and "error" not in got and time.time() < deadline:
        time.sleep(0.4)
    srv.shutdown()
    if "code" not in got:
        return {"ok": False, "msg": "인증 취소됐거나 시간이 지났어(" + got.get("error", "무응답") + ")."}

    try:
        tok = _post(TOKEN_URL, {"code": got["code"], "client_id": cfg["client_id"],
                                "client_secret": cfg["client_secret"],
                                "redirect_uri": redirect,
                                "grant_type": "authorization_code"})
    except Exception as e:
        return {"ok": False, "msg": f"토큰 교환 실패: {e}"}
    if not tok.get("refresh_token"):
        return {"ok": False, "msg": "refresh_token 이 안 왔어(이미 허용한 계정이면 구글 계정 설정에서 "
                                    "COMET 접근 권한을 지우고 다시 해)."}
    cfg["refresh_token"] = tok["refresh_token"]
    _save(cfg)
    return {"ok": True, "msg": "구글 할 일 연결 완료. 이제 '할일 가져오기'."}


# ═══════════════════════════════════════════════════════════════
#  API 호출
# ═══════════════════════════════════════════════════════════════
def _post(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _access_token():
    if _token_cache["access"] and time.time() < _token_cache["exp"]:
        return _token_cache["access"]
    cfg = _load()
    if not (configured() and cfg.get("refresh_token")):
        raise RuntimeError("구글 할 일 인증 전이야. '할일 구글연결' 먼저.")
    tok = _post(TOKEN_URL, {"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                            "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token"})
    if "access_token" not in tok:
        raise RuntimeError("토큰 갱신 실패 — 동의화면이 Testing 이면 7일마다 만료된다(프로덕션으로 게시).")
    _token_cache["access"] = tok["access_token"]
    _token_cache["exp"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    return _token_cache["access"]


def _get(path, **params):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _access_token()})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch():
    """구글 할 일 전체(미완료만) → [{id, title, due, list}]. due 는 'YYYY-MM-DD' 또는 None."""
    out = []
    lists = _get("/users/@me/lists", maxResults=100).get("items") or []
    for tl in lists:
        page = None
        while True:
            params = {"showCompleted": "false", "showHidden": "false", "maxResults": 100}
            if page:
                params["pageToken"] = page
            data = _get(f"/lists/{tl['id']}/tasks", **params)
            for t in data.get("items") or []:
                title = (t.get("title") or "").strip()
                if not title:
                    continue                      # 제목 없는 빈 줄은 구글에서도 흔하다
                out.append({"id": t["id"], "title": title,
                            "due": (t.get("due") or "")[:10] or None,
                            "list": tl.get("title") or ""})
            page = data.get("nextPageToken")
            if not page:
                break
    return out


# ═══════════════════════════════════════════════════════════════
#  COMET 할일로 들여오기 (한 방향, 중복 없이)
# ═══════════════════════════════════════════════════════════════
def sync(log=print):
    """구글 → COMET. 이미 들여온 건 제목·마감만 갱신하고, 구글에서 사라졌으면 닫는다.

    구글로 되돌려 쓰지 않는다(읽기 전용 스코프) — 한쪽으로만 흐르게 해서 꼬임을 막는다.
    """
    import todo_box
    try:
        items = fetch()
    except Exception as e:
        return {"ok": False, "msg": f"구글 할 일 가져오기 실패: {e}"}

    added = updated = closed = 0
    with todo_box._conn() as c:                   # 컬럼 마이그레이션(due·src)은 여기서 한다
        now = datetime.now().isoformat(timespec="seconds")
        seen = set()
        for it in items:
            src = "gtasks:" + it["id"]
            seen.add(src)
            row = c.execute("SELECT id, content, due, status FROM memory WHERE src=?",
                            (src,)).fetchone()
            tag = todo_box.guess_project(it["title"] + " " + it["list"])
            if row:
                if row["content"] != it["title"] or row["due"] != it["due"] or row["status"] != "open":
                    c.execute("UPDATE memory SET content=?, due=?, status='open', updated=?, tag=? "
                              "WHERE id=?", (it["title"], it["due"], now, tag, row["id"]))
                    updated += 1
            else:
                c.execute("INSERT INTO memory(kind, content, status, created, updated, tag, due, src) "
                          "VALUES('todo', ?, 'open', ?, ?, ?, ?, ?)",
                          (it["title"], now, now, tag, it["due"], src))
                added += 1
        # 구글에서 없어진(= 거기서 끝냈거나 지운) 것은 여기서도 닫는다
        for row in c.execute("SELECT id, src FROM memory WHERE kind='todo' AND status='open' "
                             "AND src LIKE 'gtasks:%'").fetchall():
            if row["src"] not in seen:
                c.execute("UPDATE memory SET status='done', updated=? WHERE id=?", (now, row["id"]))
                closed += 1

    todo_box.export()
    cfg = _load()
    cfg["last_sync"] = datetime.now().isoformat(timespec="minutes")
    _save(cfg)
    msg = f"구글 할 일 {len(items)}건 — 새로 {added}, 갱신 {updated}, 닫음 {closed}."
    if any(i["due"] is None for i in items):
        msg += " (구글 할 일엔 시각이 없어서 마감 날짜만 들어와)"
    log(msg)
    return {"ok": True, "added": added, "updated": updated, "closed": closed, "msg": msg}


def command(text):
    """'할일 가져오기' / '할일 구글연결' / '할일 구글' 을 todo_box 보다 먼저 가로챈다."""
    flat = re.sub(r"\s+", "", text or "")
    if re.search(r"^(할일|투두)?(구글|google)(할일|태스크|tasks)?(연결|인증|로그인)$", flat):
        return authorize()["msg"]
    if re.search(r"^(할일|투두)?(구글|google)?(할일)?(가져오기|가져와|동기화|싱크|sync)$", flat):
        if not configured():
            return status()
        return sync(log=lambda s: None)["msg"]
    if flat in ("할일구글", "구글할일", "할일구글상태"):
        return status()
    return None


if __name__ == "__main__":
    import sys
    if "--auth" in sys.argv:
        print(authorize()["msg"])
    else:
        print(status())
