# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 데몬 — HTTP 서버 (표준 라이브러리만, 의존성 0)
#   상주하며 /chat 요청을 처리. 같은 와이파이의 폰 브라우저로 바로 접속.
#   GET  /      → 웹 채팅 페이지
#   POST /chat  → {"text":"..."} → {"reply":"..."}
#  음성은 PC 로컬(run.bat)에서. 데몬은 텍스트 전용 v1.
#  주의: LAN에 인증 없이 열림 — 집 와이파이 내 사용 전제. 외부 노출 시 터널+인증 필요.
# ═══════════════════════════════════════════════════════════════
import os
import sys
import json
import socket
import secrets
import threading
import hmac
import base64
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 데몬 기동 시 호윤 최신 메모리 → profile/ 자동 동기화 (코멧이 항상 최신으로 깨어남).
#  ★comet import 전에 해야 SYSTEM(_PROFILE)이 갱신본을 로드한다. 복사·갱신만(미러 아님=안전).
try:
    import profile_sync
    print("[profile] " + profile_sync.sync().splitlines()[0])
except Exception as _e:
    print(f"[profile] 동기화 건너뜀: {_e}")

import comet
import localcfg

HOST = "127.0.0.1"     # localhost 전용 — 외부 노출 0. 원격 접속은 'tailscale serve'(HTTPS)로.
PORT = 8765
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daemon_token.txt")

_comet = comet.Comet()
_comet.auto_approve = True        # 데몬(폰)은 input() 불가 → 자동승인. 콘솔은 False(기본).
_lock = threading.Lock()          # 단일 사용자 — 요청 직렬화로 history 보호


# ── 접속 토큰: 없으면 생성, 있으면 로드 (이 파일은 공유 금지) ──
def _load_or_create_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


TOKEN = _load_or_create_token()

# 원격(serve 경유) 허용 계정 — Tailscale이 위조 불가하게 박아주는 본인 로그인
#  local_config.json 의 allowed_logins 를 먼저 보고, 없으면 환경변수 COMET_ALLOWED_LOGINS(쉼표 구분).
#  둘 다 비어 있으면 원격 접속은 전부 거부된다(로컬 접속은 영향 없음).
ALLOWED_LOGINS = set(localcfg.get("allowed_logins") or
                     [e.strip() for e in os.environ.get("COMET_ALLOWED_LOGINS", "").split(",") if e.strip()])


WEB_UI = """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>COMET</title>
<meta name="theme-color" content="#0e0f12">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="COMET">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,system-ui,sans-serif; background:#0e0f12; color:#e8e8ea;
         display:flex; flex-direction:column; height:100dvh; }
  header { padding:calc(14px + env(safe-area-inset-top)) 16px 14px; font-weight:700; letter-spacing:1px;
           border-bottom:1px solid #23252b; display:flex; justify-content:space-between; align-items:center; }
  .ico { background:transparent; border:none; color:#e8e8ea; font-size:20px; padding:4px 8px; cursor:pointer; }
  #log { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:80%; padding:10px 13px; border-radius:14px; white-space:pre-wrap; line-height:1.45; }
  .me  { align-self:flex-end; background:#2a6df4; color:#fff; border-bottom-right-radius:4px; }
  .ai  { align-self:flex-start; background:#1c1e24; border-bottom-left-radius:4px; }
  .sys { align-self:center; color:#6b6e76; font-size:12px; }
  form { display:flex; gap:8px; padding:12px 12px calc(12px + env(safe-area-inset-bottom)); border-top:1px solid #23252b; }
  input { flex:1; padding:12px; border-radius:12px; border:1px solid #2c2f37; background:#15171c; color:#e8e8ea; font-size:16px; }
  button { padding:0 18px; border:none; border-radius:12px; background:#2a6df4; color:#fff; font-size:16px; font-weight:600; }
  button:disabled { opacity:.5; }
</style></head><body>
<header><span>COMET</span><span><button id="conv" class="ico" title="연속 음성 대화">💬</button><button id="spk" class="ico" title="읽어주기 켜기/끄기">🔇</button></span></header>
<div id="log"><div class="msg sys">COMET 데몬에 연결됨</div></div>
<form id="f"><button id="cam" class="ico" type="button" title="사진 역검색">📷</button><input id="img" type="file" accept="image/*" style="display:none"><button id="mic" class="ico" type="button" title="음성 입력">🎤</button><input id="t" autocomplete="off" placeholder="메시지..." autofocus><button id="b">전송</button></form>
<script>
// serve 경유면 본인 인증 자동 → 토큰 불필요. 비-serve에서 401이면 토큰 1회 묻기.
const log=document.getElementById('log'),t=document.getElementById('t'),f=document.getElementById('f'),b=document.getElementById('b');
const spk=document.getElementById('spk'),mic=document.getElementById('mic'),conv=document.getElementById('conv');
function add(text,cls){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
function authHdr(){const k=localStorage.getItem('comet_token');return k?{'Authorization':'Bearer '+k}:{};}
async function send(text){return fetch('/chat',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},authHdr()),body:JSON.stringify({text})});}

let speakOn=false,convoMode=false,speaking=false,handling=false;
const player=new Audio();
// 읽어주기(TTS): 데몬이 InJoon mp3 생성 → 재생 (모든 기기 동일 목소리)
async function say(text,after){
  if(!speakOn){if(after)after();return;}
  try{
    const r=await fetch('/tts',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},authHdr()),body:JSON.stringify({text})});
    if(!r.ok)throw 0;
    const url=URL.createObjectURL(await r.blob());
    speaking=true;player.src=url;
    player.onended=player.onerror=()=>{speaking=false;URL.revokeObjectURL(url);if(after)after();};
    await player.play();
  }catch(e){speaking=false;if(after)after();}
}
spk.onclick=()=>{speakOn=!speakOn;spk.textContent=speakOn?'🔊':'🔇';
  if(!speakOn){try{player.pause();}catch(e){}speaking=false;}
  else{try{player.play().catch(()=>{});}catch(e){}}};

// 음성 입력(STT) — 안드로이드 크롬 지원, iOS 사파리는 제한적
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
let rec=null;
if(SR){rec=new SR();rec.lang='ko-KR';rec.interimResults=false;rec.maxAlternatives=1;
  rec.onresult=e=>{handling=true;mic.textContent='🎤';submitText(e.results[0][0].transcript);};
  rec.onerror=()=>{mic.textContent='🎤';};
  rec.onend=()=>{mic.textContent='🎤';if(convoMode&&!speaking&&!handling)startListen();};}
else{mic.style.display='none';conv.style.display='none';}
function stopSpeaking(){try{player.pause();}catch(e){}speaking=false;}
// 재생 중에 🎤 누르면 말 끊고 바로 듣기(barge-in). 기다릴 필요 없음.
function startListen(){if(!rec)return;if(speaking)stopSpeaking();handling=false;try{rec.start();mic.textContent='🔴';}catch(e){}}
mic.onclick=()=>startListen();
// SR 없는 기기(iOS 등)는 재생 화면 탭으로 끊기
log.onclick=()=>{if(speaking)stopSpeaking();};
// 연속 대화: 켜면 답변 후 자동으로 다시 듣기(키보드 안 뜸)
conv.onclick=()=>{convoMode=!convoMode;conv.textContent=convoMode?'🔴':'💬';
  if(convoMode){speakOn=true;spk.textContent='🔊';startListen();}
  else{try{rec&&rec.stop();}catch(e){}if(window.speechSynthesis)speechSynthesis.cancel();}};

async function submitText(text){text=(text||'').trim();if(!text){handling=false;return;}
  add(text,'me');t.value='';b.disabled=true;const wait=add('…','ai');
  try{let r=await send(text);
    if(r.status===401){const k=prompt('이 접속은 본인 인증이 안 됨. 토큰 입력(로컬 콘솔용):');
      if(k){localStorage.setItem('comet_token',k.trim());r=await send(text);}}
    if(r.status===401){localStorage.removeItem('comet_token');wait.textContent='[인증 실패]';handling=false;}
    else{const j=await r.json();const reply=j.reply||j.error||'(빈 응답)';wait.textContent=reply;
      say(reply,()=>{handling=false;if(convoMode)startListen();});}}
  catch(err){wait.textContent='[연결 오류] '+err;handling=false;}
  b.disabled=false;if(!convoMode)t.focus();log.scrollTop=log.scrollHeight;}
f.onsubmit=e=>{e.preventDefault();submitText(t.value);};

// 사진 역검색: 📷 → 파일/카메라 선택 → base64 업로드 → /imgsearch
const img=document.getElementById('img'),cam=document.getElementById('cam');
cam.onclick=()=>img.click();
img.onchange=async()=>{const file=img.files[0];img.value='';if(!file)return;
  const hint=(t.value||'').trim();t.value='';
  add('📷 '+file.name+(hint?'  ('+hint+')':''),'me');
  b.disabled=true;cam.disabled=true;const wait=add('이미지 역검색 중… (조금 걸려)','ai');
  try{
    const b64=await new Promise((res,rej)=>{const fr=new FileReader();
      fr.onload=()=>res((fr.result+'').split(',')[1]);fr.onerror=rej;fr.readAsDataURL(file);});
    let r=await fetch('/imgsearch',{method:'POST',
      headers:Object.assign({'Content-Type':'application/json'},authHdr()),
      body:JSON.stringify({image_b64:b64,filename:file.name,hint})});
    if(r.status===401){const k=prompt('본인 인증 안 됨. 토큰 입력:');
      if(k){localStorage.setItem('comet_token',k.trim());
        r=await fetch('/imgsearch',{method:'POST',headers:Object.assign({'Content-Type':'application/json'},authHdr()),body:JSON.stringify({image_b64:b64,filename:file.name,hint})});}}
    if(r.status===401){localStorage.removeItem('comet_token');wait.textContent='[인증 실패]';}
    else{const j=await r.json();const reply=j.reply||j.error||'(빈 응답)';wait.textContent=reply;say(reply);}}
  catch(err){wait.textContent='[업로드 오류] '+err;}
  b.disabled=false;cam.disabled=false;log.scrollTop=log.scrollHeight;};
// PWA: 홈 화면 추가 시 앱처럼 동작 (서비스워커 등록, 실패해도 무시)
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{});}
</script></body></html>"""


# ── PWA: 매니페스트 + 서비스워커 + 아이콘 (홈 화면에 추가 시 앱처럼) ──
MANIFEST = json.dumps({
    "name": "COMET",
    "short_name": "COMET",
    "description": "호윤의 상주 비서 AI",
    "lang": "ko",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#0e0f12",
    "theme_color": "#0e0f12",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}, ensure_ascii=False)

# 셸/아이콘만 네트워크-우선 캐시(UI 갱신 즉시 반영 + 오프라인 폴백). /chat·/tts·/ping은 통과.
SERVICE_WORKER = """
const CACHE='comet-v1';
const SHELL=['/','/manifest.webmanifest','/icon-180.png','/icon-192.png','/icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(SHELL)).then(()=>self.skipWaiting()));});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));});
self.addEventListener('fetch',e=>{
  const req=e.request;
  if(req.method!=='GET')return;
  const url=new URL(req.url);
  if(url.pathname==='/ping')return;
  e.respondWith(fetch(req).then(r=>{const cp=r.clone();caches.open(CACHE).then(c=>c.put(req,cp));return r;}).catch(()=>caches.match(req)));
});
"""

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_uploads")


def _load_static():
    s = {
        "/manifest.webmanifest": (MANIFEST.encode("utf-8"), "application/manifest+json; charset=utf-8"),
        "/sw.js": (SERVICE_WORKER.encode("utf-8"), "text/javascript; charset=utf-8"),
    }
    for name in ("icon-180.png", "icon-192.png", "icon-512.png", "icon-512-maskable.png"):
        p = os.path.join(WEB_DIR, name)
        if os.path.isfile(p):
            with open(p, "rb") as fp:
                s["/" + name] = (fp.read(), "image/png")
    return s


STATIC = _load_static()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, code, data, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, WEB_UI, "text/html")
        elif self.path == "/ping":
            self._send(200, json.dumps({"ok": True}))
        elif self.path in STATIC:
            data, ctype = STATIC[self.path]
            self._send_bytes(200, data, ctype)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _authorized(self):
        # 1) Tailscale 본인 인증 (serve 경유 — Tailscale이 박아줘서 원격 위조 불가)
        login = self.headers.get("Tailscale-User-Login", "")
        if login and login in ALLOWED_LOGINS:
            return True
        # 2) 토큰 (로컬 콘솔/직접 접속용)
        h = self.headers.get("Authorization", "")
        tok = h[7:] if h.startswith("Bearer ") else self.headers.get("X-Token", "")
        return bool(tok) and hmac.compare_digest(tok, TOKEN)

    def do_POST(self):
        if self.path not in ("/chat", "/tts", "/imgsearch"):
            self._send(404, json.dumps({"error": "not found"}))
            return
        if not self._authorized():
            self._send(401, json.dumps({"error": "unauthorized"}))
            return

        # /imgsearch: 폰에서 올린 사진(base64) → 임시파일 → 이미지 역검색(heavy 27b 종합)
        if self.path == "/imgsearch":
            self._handle_imgsearch()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            text = json.loads(self.rfile.read(length).decode("utf-8")).get("text", "").strip()
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return
        if not text:
            self._send(400, json.dumps({"error": "empty"}))
            return

        # /tts: 텍스트 → InJoon mp3 (히스토리 안 건드림 → lock 불필요)
        if self.path == "/tts":
            try:
                import voice
                audio = voice.synth_mp3(text)
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
                return
            self._send_bytes(200, audio, "audio/mpeg")
            return

        # /chat
        print(f"\n[요청] {text}")
        with _lock:
            try:
                reply = _comet.respond(text, speak=False) or ""
            except Exception as e:
                reply = f"[오류] {e}"
        self._send(200, json.dumps({"reply": reply}, ensure_ascii=False))

    def _handle_imgsearch(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            b64 = data.get("image_b64", "")
            hint = (data.get("hint") or "").strip()
            fname = (data.get("filename") or "upload.jpg")
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return
        if not b64:
            self._send(400, json.dumps({"error": "no image"}))
            return

        # 확장자 추출(없으면 .jpg). 임시파일은 _uploads/ 에 저장 후 삭제.
        ext = os.path.splitext(fname)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            ext = ".jpg"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        tmp = os.path.join(UPLOAD_DIR, f"img_{int(time.time()*1000)}{ext}")
        try:
            with open(tmp, "wb") as fp:
                fp.write(base64.b64decode(b64))
        except Exception as e:
            self._send(400, json.dumps({"error": f"decode fail: {e}"}))
            return

        print(f"\n[이미지 역검색] {fname}" + (f"  힌트: {hint}" if hint else ""))
        with _lock:
            try:
                reply = _comet._image_search(
                    hint or "이 사진 역검색해줘", path=tmp, hint=hint) or ""
            except Exception as e:
                reply = f"[오류] {e}"
        try:
            os.remove(tmp)
        except Exception:
            pass
        self._send(200, json.dumps({"reply": reply}, ensure_ascii=False))

    def log_message(self, *a):     # 기본 액세스 로그 끔
        pass


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    ip = _lan_ip()
    print("═" * 55)
    print("  COMET 데몬 가동")
    print(f"  이 PC에서:   http://127.0.0.1:{PORT}")
    print(f"  같은 와이파이 폰/기기:  http://{ip}:{PORT}")
    print("  ─────────────────────────────────────")
    print(f"  접속 토큰:  {TOKEN}")
    print(f"  (저장 위치: {TOKEN_FILE})")
    print("  ─────────────────────────────────────")
    print("  (Ctrl+C 종료. 방화벽이 막으면 '사설 네트워크 허용')")
    print("═" * 55)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
