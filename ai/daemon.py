# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 데몬 — HTTP 서버 (표준 라이브러리만, 의존성 0)
#   상주하며 /chat 요청을 처리. 같은 와이파이의 폰 브라우저로 바로 접속.
#   GET  /      → 웹 채팅 페이지
#   POST /chat  → {"text":"..."} → {"reply":"..."}   (id 를 실으면 POST /cancel {"id"} 로 멈출 수 있다)
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


# ── 콘솔에 찍히는 걸 파일로도 남긴다 (logs/daemon-YYYY-MM-DD.log) ──
#   ★데몬 창을 닫으면 그 글자는 사라진다 — 오류가 나도 나중에 되짚을 수가 없었다
#   (2026-09-18 실제로 호윤이 본 오류를 못 찾음). 화면은 그대로 두고 파일에 겹쳐 쓴다.
class _Tee:
    def __init__(self, stream):
        self.stream = stream
        self.lock = threading.Lock()
        self.day = ""
        self.fp = None
        self.bol = True                              # 줄 첫머리인가(여기에만 시각을 붙인다)

    def _file(self):
        day = time.strftime("%Y-%m-%d")
        if self.fp is None or day != self.day:      # 자정을 넘기면 새 파일로
            if self.fp:
                try:
                    self.fp.close()
                except Exception:
                    pass
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
            os.makedirs(d, exist_ok=True)
            self.fp = open(os.path.join(d, f"daemon-{day}.log"), "a", encoding="utf-8")
            self.day = day
        return self.fp

    def write(self, s):
        with self.lock:
            try:
                self.stream.write(s)
            except Exception:
                pass
            try:
                f = self._file()
                for part in s.splitlines(keepends=True):
                    if self.bol and part.strip():
                        f.write(time.strftime("%H:%M:%S "))
                    f.write(part)
                    self.bol = part.endswith("\n")
                f.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        try:
            self.stream.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self.stream, "isatty", lambda: False)()


sys.stdout = _Tee(sys.stdout)
sys.stderr = _Tee(sys.stderr)
print("═" * 55)
print(f"[로그] 이 창의 내용은 logs/daemon-{time.strftime('%Y-%m-%d')}.log 에도 쌓인다")


# ── 데몬은 한 PC 에 하나만 ──
#   ★윈도우에선 두 번째 데몬이 8765 를 오류 없이 '같이' 잡는다 — HTTPServer 가 SO_REUSEADDR 를 켜기 때문
#     (2026-10-07 실측: 같은 포트에 두 번째 ThreadingHTTPServer 가 그냥 붙었다). 그러면 요청이 두 데몬에
#     엇갈려 가고, 둘이 각자 들고 있는 대화기록으로 comet_history.json 을 번갈아 통째로 덮어써 앞 대화를
#     잊는다. 대화창 「소식 듣기」를 꺼도 그 요청이 알림을 안 쥔 쪽 데몬에 가면 소식은 계속 울린다.
#     HANDOFF 의 "옛 데몬이 살아 있으면 … 옛 코드가 응답함" 이 이 모양이다.
#   → 무거운 준비(프로필·Comet()·자율모드 복원)보다 먼저 이름 있는 뮤텍스로 막는다. 창을 X 로 닫아 죽어도
#     윈도우가 뮤텍스를 같이 풀어 주니 남는 잠금이 없다. '재시작'(os.execv)은 옛 프로세스가 내려가는 데
#     잠깐 걸려서 몇 초는 기다려 준다.
def _only_one(name="Local\\COMET_DAEMON", wait=8.0):
    """처음 뜬 데몬이면 뮤텍스 핸들(끝까지 쥐고 있는다), 다른 데몬이 이미 떠 있으면 None.
       윈도우가 아니거나 뮤텍스를 못 만들면 True — 막지 않고 그냥 뜬다(못 뜨는 것보다 낫다)."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)    # 따로 불러온 사본 — argtypes 가 남과 안 엉키게
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    dead = time.time() + wait
    while True:
        h = k32.CreateMutexW(None, False, name)
        if not h:
            return True
        if ctypes.get_last_error() != 183:                   # ERROR_ALREADY_EXISTS 가 아니면 우리가 처음
            return h
        k32.CloseHandle(h)
        if time.time() >= dead:
            return None
        time.sleep(0.5)


_ONE = _only_one()
if _ONE is None:
    print("[데몬] 다른 COMET 데몬이 이미 떠 있다 — 이 창은 그냥 닫으면 된다.")
    print("       (둘이 같이 돌면 대화기록·소식 알림이 꼬인다. 새 코드로 바꾸려면 떠 있는 데몬 창을 먼저 닫고 다시 켤 것)")
    sys.exit(0)

# ── 로그인 전 코멧(세션 0 · 작업 스케줄러 COMET-boot) — headless.py 머리말 ──
import headless
if headless.HEADLESS:
    if headless.user_logged_on() and os.environ.get("COMET_HEADLESS") != "1":
        print("[로그인 전] 이미 누가 로그인해 있다 — 로그인 쪽 코멧(comet_logon.py)에 맡기고 내려감")
        sys.exit(0)                     # 0 = doctor 도 그대로 끝
    try:
        os.remove(headless.STOP_FLAG)   # 지난번에 남은 내려가라 표시
    except OSError:
        pass
    with open(headless.PID_FILE, "w", encoding="utf-8") as _f:
        _f.write(str(os.getpid()))

    def _watch_stop():
        """로그인하면 comet_logon.py 가 STOP_FLAG 를 놓는다 → 답하던 건 마치고(대화 줄) 스스로 내려감."""
        while not os.path.exists(headless.STOP_FLAG):
            time.sleep(2)
        print("[로그인 전] 로그인함 — 로그인 쪽 코멧에 넘기고 내려감")
        lk = globals().get("_lock")
        if lk is not None:
            lk.acquire(timeout=180)
        try:
            os.remove(headless.PID_FILE)
        except OSError:
            pass
        sys.stdout.flush()
        os._exit(0)
    threading.Thread(target=_watch_stop, daemon=True).start()
    print("[로그인 전] 화면 없는 코멧 — 텔레그램 · 폰 웹 · 브리핑만(목소리 · Alt 두 번 없음)")

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
import restart                                 # 업그레이드 적용 · 되돌리기 뒤 스스로 껐다 켜기(10/14)
restart.ENABLED, restart.LOCK = True, _lock
_comet.autonomy.modes["talk"].lock = _lock   # Alt 두 번 말걸기도 폰 요청과 같은 줄에 선다
_comet.autonomy.modes["telegram"].lock = _lock   # 텔레그램으로 온 질문도 같은 줄(답할 때마다 읽으니 복원 뒤에 꽂아도 된다)


# ── 창을 X 로 닫아도, 줄여 둔 다른 앱 볼륨은 되돌리고 죽는다 ──
#   ★X 로 닫으면 파이썬 finally·atexit 이 안 돈다(그 자리에서 죽음). 코멧이 말하는 도중에 닫으면 다른 앱이
#     20% 로 줄어든 채 남고, 다음에 데몬을 켤 때에야 아래 recover() 가 되돌렸다 — 로그의 '[덕킹] 지난번에
#     줄인 채 끝난 앱 볼륨을 되돌림'(9/23 01:40 · 10/01 15:22 · 10/07 07:15). 10/07 은 06:02 에 말하는 도중
#     닫고 07:15 에 다시 켤 때까지 73분 동안 줄어 있었다.
#   윈도우는 창을 닫을 때 '콘솔 닫기' 신호를 보내고 처리기가 끝날 때까지 몇 초 기다려 준다 — 거기서 같은 recover().
#   Ctrl+C 도 이 처리기를 지난다. '안 받은 걸로'(False) 돌려줘서 원래 동작(종료·KeyboardInterrupt)은 그대로.
_CTRL_HANDLER = None


def _on_console_close(kind):
    """콘솔 신호 처리기 — 0 Ctrl+C · 1 Ctrl+Break · 2 창 닫기 · 5 로그오프 · 6 종료. 윈도우가 따로 만든 스레드에서 돈다."""
    try:
        d = _voice.ducker
        got = d.lock.acquire(timeout=1.0)     # on() 은 lock 을 쥔 채 줄인다 — 줄이는 중(0.15초)이면 다 줄인 뒤에
        try:                                  # (줄이다 만 값은 recover 가 '내가 바꾼 것'으로 보고 안 되돌린다)
            n = d.recover()                   # 파일에 적어 둔 원래 값으로 · 그 사이 내가 바꾼 앱은 안 건드림
        finally:
            if got:
                d.lock.release()
        if n:
            print(f"[덕킹] 끄는 중 — 줄여 둔 앱 볼륨 {n}개 되돌림")
    except Exception:
        pass
    return False


def _hook_console_close():
    global _CTRL_HANDLER
    if os.name != "nt" or _CTRL_HANDLER is not None:
        return False
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    k32.SetConsoleCtrlHandler.argtypes = (PROC, wintypes.BOOL)
    k32.SetConsoleCtrlHandler.restype = wintypes.BOOL
    _CTRL_HANDLER = PROC(_on_console_close)   # 참조를 쥐고 있어야 GC 가 콜백을 안 치운다
    return bool(k32.SetConsoleCtrlHandler(_CTRL_HANDLER, True))


try:
    import voice as _voice
    if headless.HEADLESS:
        raise RuntimeError("로그인 전 — 소리 장치 없음")
    if _voice.ducker.recover():
        print("[덕킹] 지난번에 줄인 채 끝난 앱 볼륨을 되돌림")
    if not _hook_console_close() and os.name == "nt":
        print("[덕킹] 창 닫기 처리기를 못 걸었다 — 말하는 도중 닫으면 다음에 켤 때 되돌린다")
except Exception as _e:
    print(f"[덕킹] 복구 건너뜀: {_e}")
try:
    if headless.HEADLESS:
        raise RuntimeError("로그인 전 — 소리 장치 없음")
    _voice.warm_ayaka()           # 목소리 서버를 미리 — 첫 말이 Edge 로 새지 않게(서버는 데몬과 따로 산다)
except Exception as _e:
    print(f"[아야카] 미리 띄우기 건너뜀: {_e}")


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


_speak_lock = threading.Lock()
_news_lock = threading.Lock()

# 대화창 Esc = 보낸 말 멈추기(10/15) — 대화창이 /chat 에 id 를 실어 보내면 그 요청만 끊을 수 있다.
#   ★comet.CANCEL 은 말걸기 창(Alt 두 번)과 같이 쓰는 스위치라, 지금 줄 안에서 도는 게 그 id 일 때만 켠다
#     (폰·텔레그램·말걸기 차례에 대화창 Esc 가 남의 생성을 끊으면 안 된다).
_cancel_lock = threading.Lock()
_chat_now = None              # 지금 _lock 안에서 도는 대화창 요청 id
_chat_waiting = set()         # _lock 앞에 줄 서 있는 대화창 요청 id
_chat_dropped = set()         # 줄 서 있는 사이 Esc 로 거둔 id — 차례가 와도 안 돌린다


def _speak_async(text):
    """대답을 소리로 읽어준다 — 별도 스레드.

    ★HTTP 응답 안에서 읽으면 재생이 끝날 때까지 대화창에 글자가 안 뜬다(아야카 합성 2초 +
    재생 수 초). 그래서 글자를 먼저 보내고 여기서 읽는다. Mouth 는 콘솔·Alt 말걸기와
    같은 것을 쓴다(덕킹·형체·중단이 한 곳에서 돌게). 두 답이 겹쳐 읽히지 않게 잠근다.
    """
    if not text or text.startswith("[오류]"):
        return

    def run():
        with _speak_lock:
            try:
                _comet._get_mouth().speak(text)
            except Exception as e:
                print(f"  [대화창 음성 오류] {e}")

    threading.Thread(target=run, daemon=True).start()


def _work_now():
    """코멧이 지금 하고 있는 일 — /news 답에 실어 대화창이 혜성 애니메이션 · 머리말로 보여 준다
       (10/15 호윤 「뭔가 하고 있는 중일 때 배경에 혜성 … 그러면 작동하고 있는 거 알 듯」).
       3초마다 불리니 가벼운 것만 본다 — 대화 줄 · 회의 · 이 데몬 안에서 도는 업그레이드."""
    work = []
    with _cancel_lock:                             # Esc 로 멈춘 대화창 요청이 남은 계산을 마저 도는 중이면 일로 안 친다
        stopped = _chat_now is not None and _chat_now in _chat_dropped     # (10/15 Esc 뒤에도 혜성이 남았다)
    if _lock.locked() and not stopped:
        work.append("대화")                        # 대화창 · 텔레그램 · 말걸기 · 폰 중 하나가 답 만드는 중
    if getattr(_comet, "_debating", False):
        work.append("회의")
    try:
        import upgrade
        j = upgrade._load().get("job") or {}
        if (j.get("status") in ("running", "fixing") and j.get("pid") == os.getpid()
                and upgrade._alive(j["pid"])):     # 꺼졌다 뜬 뒤 남은 'running' 표시는 빼고
            work.append(f"업그레이드 · {j.get('step') or j.get('status')}")
    except Exception:
        pass
    return work


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
        if self.path not in ("/chat", "/tts", "/imgsearch", "/news", "/cancel"):
            self._send(404, json.dumps({"error": "not found"}))
            return
        if not self._authorized():
            self._send(401, json.dumps({"error": "unauthorized"}))
            return

        # /imgsearch: 폰에서 올린 사진(base64) → 임시파일 → 이미지 역검색(heavy 27b 종합)
        if self.path == "/imgsearch":
            self._handle_imgsearch()
            return
        # /news: 대화창 「소식 듣기」 버튼 — 대화 줄(_lock)에 안 선다(코멧이 생각 중이어도 바로 먹게)
        if self.path == "/news":
            self._handle_news()
            return
        # /cancel: 대화창 Esc — 이것도 줄에 안 선다(줄 안에서 생각 중인 걸 끊으러 오니까)
        if self.path == "/cancel":
            self._handle_cancel()
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            _body = json.loads(self.rfile.read(length).decode("utf-8"))
            text = (_body.get("text") or "").strip()
            want_speak = bool(_body.get("speak"))     # PC 대화창만 보낸다(폰은 /tts 로 따로)
            rid = str(_body.get("id") or "")          # 대화창만 — 있으면 Esc 로 멈출 수 있는 요청
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
        global _chat_now
        print(f"\n[요청] {text}")
        if rid:
            with _cancel_lock:
                _chat_waiting.add(rid)
        with _lock:
            with _cancel_lock:
                _chat_waiting.discard(rid)
                dropped = rid in _chat_dropped
                _chat_dropped.discard(rid)
                if rid and not dropped:
                    _chat_now = rid
            if dropped:
                print("  [Esc] 차례 오기 전에 거둠 — 안 돌림")
                self._send(200, json.dumps({"reply": "", "cancelled": True}))
                return
            n = len(_comet.history.msgs)
            try:
                if rid:
                    # 멈추면 None — 이 턴은 대화 기록에서도 빠진다(잘못 친 말을 코멧이 기억하지 않게)
                    reply = _comet.respond_cancelable(text)
                else:
                    reply = _comet.respond(text, speak=False) or ""
            except Exception as e:
                reply = f"[오류] {e}"
            finally:
                with _cancel_lock:
                    _chat_now = None
                    stopped = rid in _chat_dropped      # respond 는 원래도 None 을 줄 때가 있다 — 멈춤 여부는 /cancel 로 가린다
                    _chat_dropped.discard(rid)
            if stopped:
                # ★respond_cancelable 이 CANCEL 을 본 뒤(답·기록 저장까지 끝난 뒤)에 /cancel 이 오면 기록이 남는다 —
                #   /cancel 엔 '멈춤' 이라고 답했으니 여기서 마저 지운다. 늦게 켜진 CANCEL 도 꺼야 다음 사람 답이 안 끊긴다.
                comet.CANCEL.clear()
                if len(_comet.history.msgs) > n:
                    del _comet.history.msgs[n:]
                    try:
                        _comet.history.save()
                    except Exception:
                        pass
                print("  [Esc] 대화창에서 멈춤 — 이 턴은 기록에서 뺌")
                self._send(200, json.dumps({"reply": "", "cancelled": True}))
                return
            reply = reply or ""
            # 모델 답은 respond 가 「COMET: …」로 이미 찍는다(대화 기록에도 들어감). 명령 답(업그레이드 · 할일 …)은
            # 기록에 안 들어가고 콘솔에도 안 나와서 [요청] 줄만 덩그러니 남았다(10/14) — 그것만 찍는다.
            last = (_comet.history.msgs[-1:] or [{}])[0]
            if reply and not (last.get("role") == "assistant" and last.get("content") == reply):
                print(f"COMET: {reply[:500]}" + (f" …(전체 {len(reply)}자)" if len(reply) > 500 else ""))
        self._send(200, json.dumps({"reply": reply}, ensure_ascii=False))
        if want_speak:
            _speak_async(reply)        # 글자를 먼저 보내고, 읽기는 뒤에서

    def _handle_news(self):
        """{"on": true|false} = 소식 듣기 켜기/끄기('알림 켜/끔' 과 같은 스위치), 비우면 지금 상태만.
           {"after": id} = 그 뒤로 알린 소식(feed)도 같이 — 대화창이 말풍선으로 그린다. 답의 last 를 다음 after 로."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return
        try:
            a = _comet.autonomy
            if isinstance(data.get("on"), bool):
                with _news_lock:                     # 버튼 연타 — 켜고 끄기가 엇갈리지 않게 한 번에 하나
                    st = a.issues_toggle(data["on"])
                print(f"\n[소식 듣기] {'켬' if data['on'] else '끔'} (대화창 버튼)")
            else:
                st = a.issues_state()
            import issues
            after = data.get("after")
            st["feed"], st["last"] = issues.feed_after(after if isinstance(after, int) else None)
            st["work"] = _work_now()
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
            return
        self._send(200, json.dumps(st, ensure_ascii=False))

    def _handle_cancel(self):
        """{"id": 대화창 요청 id} — 생각 중이면 생성을 끊고(comet.CANCEL), 아직 줄 서 있으면 차례가 와도 안 돌린다.
           ★모델 스트림은 바로 멈추지만 도구·클라우드 길은 끝까지 돈 뒤 버려진다(답은 안 보내고 기록에서도 뺀다)."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            rid = str(json.loads(self.rfile.read(length).decode("utf-8") or "{}").get("id") or "")
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return
        if not rid:
            self._send(400, json.dumps({"error": "no id"}))
            return
        with _cancel_lock:
            if rid == _chat_now:
                comet.CANCEL.set()
                state = "stopping"
            elif rid in _chat_waiting:
                state = "dropped"
            else:
                state = "done"                      # 이미 답까지 끝났다(또는 모르는 id) — 못 멈춘다, 답은 /chat 으로 간다
            if state != "done":
                _chat_dropped.add(rid)              # 도는 중이면 끝난 뒤 '멈춤' 으로(기록도 뺌), 줄 서 있으면 안 돌림
        self._send(200, json.dumps({"ok": state != "done", "state": state}))

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


class _Server(ThreadingHTTPServer):
    # ★SO_REUSEADDR 를 끈다 — 윈도우에선 이게 켜져 있으면 남이 쥔 포트에도 '같이' 붙는다(위 _only_one).
    #   끄면 이미 쥔 포트는 10048 로 실패한다. 방금 닫은 데몬이 남긴 TIME_WAIT 는 윈도우에선 바인드를 안 막는다
    #   (10/07 실측 — 연결을 받고 닫은 직후 다시 바인드 성공). 뮤텍스가 없는 옛 데몬과 겹칠 때의 마지막 방어선.
    allow_reuse_address = False


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
    try:
        srv = _Server((HOST, PORT), Handler)
    except OSError as e:
        print(f"[데몬] {PORT} 포트를 다른 프로세스가 쥐고 있다 — {e}")
        print("       옛 데몬 창이 남아 있으면 그 창을 닫고 다시 켤 것. 이 창은 닫으면 된다.")
        sys.stdout.flush()
        os._exit(3)        # 3 = 코드 탓 아님(doctor.py 가 고치려 들지 않게) · 이미 띄운 자율모드 스레드(소식·말걸기·브리핑)까지 같이 내린다 — 포트 없이 알림만 울리지 않게
    # 떠 있는 동안 유휴 절전을 막는다(10/15 정전 뒤 로그인 화면에서 15분 만에 잠들어 밖에서 못 닿음). 끄면 윈도우 설정 그대로.
    print("[절전] 코멧이 떠 있는 동안 절전 막음" if headless.keep_awake() else "[절전] 막기 실패 — 윈도우 설정대로 잠든다")
    srv.serve_forever()


if __name__ == "__main__":
    main()
