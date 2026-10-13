"""COMET 전화 비서 — 호윤 본인의 S26+(업무번호)로 전화를 걸고 AI(Sonnet 5.5 API)가 대신 통화한다.

호윤이 직접 지시한 번호 · 용건으로만 건다. 첫마디에 AI 비서라고 밝힌다(사람인 척 금지).
경로: PC ↔ S26+ 블루투스 핸즈프리(HFP). 상대 목소리 = HFP 입력 장치, 코멧 목소리 = HFP 출력 장치.
걸기 · 끊기 · ARS 번호 = adb(무선 디버깅).
본인확인 · 결제 · 담당자 연결 등 호윤이 필요한 순간 → 알림 → 호윤 PC 마이크/스피커를 통화에 잇는다(중계).

    python call_agent.py 01012345678 --goal "용건" [--max-turns 20] [--test]

키는 Desktop\\api\\api.txt 에서 읽기만(환경변수 금지 — 메모리 feedback_api_key_never_in_env).
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np
import sounddevice as sd

HERE = os.path.dirname(os.path.abspath(__file__))
COMET = os.path.dirname(HERE)
ADB = r"C:\Users\USER\AppData\Local\Android\Sdk\platform-tools\adb.exe"
KEY_FILE = r"C:\Users\USER\Desktop\api\api.txt"
MODEL = "claude-sonnet-5-5"
VOICE = "ko-KR-SunHiNeural"
OWNER = "정호윤"
try:                   # 진짜 번호는 git 에 안 올리는 numbers.json 에만(공개 저장소) — {"work": 업무번호, "test": 시험 받을 번호}
    with open(os.path.join(HERE, "numbers.json"), encoding="utf-8") as _f:
        WORK_NUMBER = json.load(_f).get("work", "010-0000-0000")
except (OSError, ValueError, AttributeError):
    WORK_NUMBER = "010-0000-0000"
LOG_DIR = os.path.join(HERE, "logs")

SILENCE_SEC = 0.9      # 이만큼 조용하면 상대 말이 끝난 것
MAX_UTTER_SEC = 25
ANSWER_TIMEOUT = 60    # 받을 때까지 기다리는 시간


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ── adb ──────────────────────────────────────────────
def adb(*args, timeout=15):
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace").stdout


def dial(number):
    # 받는 앱을 telecom 으로 못 박는다 — 안 그러면 Zoom 도 CALL 을 받겠다고 나서서 「앱 고르기」 창에서 멈춘다(10/12 실측)
    adb("shell", "am", "start", "-n", "com.android.server.telecom/.components.UserCallActivity",
        "-a", "android.intent.action.CALL", "-d", f"tel:{number}")


def hangup():
    adb("shell", "input", "keyevent", "KEYCODE_ENDCALL")


def dtmf(digits):
    for ch in digits:
        code = {"*": "KEYCODE_STAR", "#": "KEYCODE_POUND"}.get(ch, f"KEYCODE_{ch}")
        adb("shell", "input", "keyevent", code)
        time.sleep(0.25)


def _telecom_states():
    """지금 통화 목록(CallsManager 의 mCalls: ~ mCallAudioManager: 사이)의 통화 상태들. 모양이 다르면 None.
    dumpsys telecom 아래쪽엔 지난 통화(Call TC@n)가 ACTIVE 로 남아 있어 전체를 보면 헛것을 잡는다."""
    out = adb("shell", "dumpsys", "telecom")
    m = re.search(r"mCalls:(.*?)mCallAudioManager:", out, re.S)
    if not m:
        return None
    return re.findall(r"\b(ACTIVE|DIALING|CONNECTING|SELECT_PHONE_ACCOUNT|RINGING|ON_HOLD|DISCONNECTING|DISCONNECTED|NEW)\b",
                      m.group(1))


def answered():
    """상대가 받았는지 — ACTIVE = 받음, DIALING 등 = 아직, 목록이 비었거나 모양이 다르면 None."""
    states = _telecom_states()
    if not states:
        return None
    return "ACTIVE" in states


def call_state():
    """0 = 통화 없음, 1 = 수신 울림, 2 = 통화 중(발신 포함), -1 = 못 읽음.
    값이 여러 줄(심 슬롯별)이면 가장 큰 값 — Phone Link 로 건 통화가 0 으로 나온 건(10/12) 첫 줄만 봐서일 수 있다(추정)."""
    m = [int(x) for x in re.findall(r"mCallState=(\d)", adb("shell", "dumpsys", "telephony.registry"))]
    return max(m) if m else -1


def in_call():
    """통화가 살아 있는지. telephony.registry 가 0 이어도 telecom 통화 목록에 살아 있는 통화가 있으면 통화 중 —
    Phone Link 로 건 통화는 mCallState 가 0 으로 나왔다(10/12 실측). 끊김 감지는 전부 이걸로 본다."""
    if call_state() in (1, 2):
        return True
    states = _telecom_states()
    return bool(states) and any(s not in ("DISCONNECTED", "DISCONNECTING") for s in states)


# ── 소리 장치 ─────────────────────────────────────────
def hfp_devices(rescan=False, allow_wdmks=True):
    """S26+ 핸즈프리 입출력 장치 번호.
    통화가 없을 땐 WDM-KS 장치만 보이고 열면 오류가 난다(2026-10-12 실측). 통화가 시작돼 폰이 소리를
    PC 로 넘기면 WASAPI/MME 장치가 생기므로 rescan=True 로 목록을 새로 읽어 그쪽을 고른다."""
    if rescan:
        sd._terminate(); sd._initialize()
    apis = sd.query_hostapis()
    pref = {"Windows WASAPI": 0, "MME": 1, "Windows DirectSound": 2, "Windows WDM-KS": 9}
    best = {"in": (99, None), "out": (99, None)}
    for i, d in enumerate(sd.query_devices()):
        if "Hands-Free" not in d["name"] and "S26" not in d["name"]:
            continue
        rank = pref.get(apis[d["hostapi"]]["name"], 5)
        if rank == 9 and not allow_wdmks:
            continue
        if d["max_input_channels"] > 0 and rank < best["in"][0]:
            best["in"] = (rank, i)
        if d["max_output_channels"] > 0 and rank < best["out"][0]:
            best["out"] = (rank, i)
    return best["in"][1], best["out"][1]


def wait_call_audio(timeout=20):
    """전화를 건 뒤 통화 소리 장치가 깨어날 때까지 기다린다. WASAPI/MME 장치가 생기면 그쪽.
    Phone Link 통화 때는 WDM-KS(77 출력 · 78 입력)만 보였다(10/12 실측) → 3초 지나도 안 생기면 통화 중일 때 WDM-KS 를 받는다.
    걸자마자는 통화 목록에 아직 안 잡힐 수 있어 처음 3초는 끊김으로 안 본다."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        late = time.time() - t0 > 3
        live = in_call() if late else True
        if late and not live:
            return None, None
        i, o = hfp_devices(rescan=True, allow_wdmks=late)
        if i is not None and o is not None:
            return i, o
        time.sleep(1)
    return None, None


class Line:
    """통화 소리 입출력. 상대 말을 문장 단위로 잘라 큐에 넣는다."""

    def __init__(self, in_dev, out_dev):
        self.in_dev, self.out_dev = in_dev, out_dev
        self._setup(int(sd.query_devices(in_dev)["default_samplerate"]),
                    int(sd.query_devices(out_dev)["default_samplerate"]))
        # 콜백 입력 — WDM-KS 는 read() 로 꺼내 읽기(blocking)를 못 한다(10/12 「Blocking API not supported」)
        kw = dict(device=in_dev, channels=1, samplerate=self.sr_in, dtype="float32", callback=self._cb)
        try:
            self.stream = sd.InputStream(blocksize=int(self.sr_in * 0.03), **kw)
        except sd.PortAudioError as e:       # 블록 크기를 장치가 못 받으면 장치가 정하는 크기로(추정 대비)
            log("입력 장치 블록 크기 거부 — 장치 기본으로:", e)
            self.stream = sd.InputStream(blocksize=0, **kw)

    def _setup(self, sr_in, sr_out):
        self.sr_in, self.sr_out = sr_in, sr_out
        self.utter = queue.Queue()
        self.speaking = threading.Event()
        self.heard_voice = threading.Event()
        self._buf, self._last_voice, self._noise = [], 0.0, 0.004

    def _cb(self, data, frames, t, status):
        if self.speaking.is_set():          # 내가 말하는 동안 들리는 건 버린다(되울림)
            return
        x = data[:, 0].copy()
        rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
        now = time.time()
        voiced = rms > max(self._noise * 3.0, 0.008)
        if not voiced and not self._buf:
            self._noise = 0.97 * self._noise + 0.03 * rms
        if voiced:
            self.heard_voice.set()
            self._last_voice = now
            self._buf.append(x)
        elif self._buf:
            self._buf.append(x)
            dur = sum(len(b) for b in self._buf) / self.sr_in
            if now - self._last_voice > SILENCE_SEC or dur > MAX_UTTER_SEC:
                audio = np.concatenate(self._buf)
                self._buf = []
                if len(audio) / self.sr_in > 0.35:
                    self.utter.put(audio)

    def start(self):
        self.stream.start()

    def stop(self):
        try:
            self.stream.stop(); self.stream.close()
        except Exception:
            pass

    def play(self, pcm):
        self.speaking.set()
        try:
            sd.play(pcm, self.sr_out, device=self.out_dev, blocking=True)
        finally:
            time.sleep(0.25)
            self.speaking.clear()


# ── 말하기 / 받아쓰기 / 두뇌 ──────────────────────────
def tts(text, sr):
    """윈도우 기본 한국어 목소리(Microsoft Heami, 오프라인 · 0.2초대).
    Edge 음성은 2026-10-12 서버가 403 으로 막아(edge-tts 7.2.8 최신인데도) 안 쓴다."""
    import pythoncom
    import soundfile as sf
    import win32com.client
    pythoncom.CoInitialize()
    v = win32com.client.Dispatch("SAPI.SpVoice")
    for tok in v.GetVoices():
        if "Heami" in tok.GetDescription():
            v.Voice = tok
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat"); fmt.Type = 18   # 16kHz 16bit mono
    fs = win32com.client.Dispatch("SAPI.SpFileStream"); fs.Format = fmt
    path = tempfile.mktemp(suffix=".wav")
    fs.Open(path, 3); v.AudioOutputStream = fs; v.Rate = 1
    v.Speak(text); fs.Close()
    pcm, src = sf.read(path, dtype="float32"); os.remove(path)
    if src != sr:
        idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * sr / src))
        pcm = np.interp(idx, np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


class Ears:
    """openai-whisper turbo, GPU 반정밀도 — 코멧 voice.Ear 와 같은 방식.
    faster-whisper 는 이 PC 에 cublas64_12.dll 이 없어 GPU 로 못 돈다(2026-10-12)."""

    def __init__(self):
        import torch
        import whisper
        cuda = torch.cuda.is_available()
        m = whisper.load_model("turbo", device="cuda" if cuda else "cpu")
        if cuda:
            m = m.half()
            for mod in m.modules():          # LayerNorm 은 float 가중치만 받는다
                if isinstance(mod, torch.nn.LayerNorm):
                    mod.float()
        self.m, self.fp16 = m, cuda

    def text(self, audio, sr):
        if sr != 16000:
            idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sr))
            audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
        peak = float(np.abs(audio).max())
        if 0 < peak < 0.3:                   # 통화 소리는 작게 들어온다 — 키워서 넣는다(20배까지)
            audio = audio * min(20.0, 0.3 / peak)
        r = self.m.transcribe(audio, language="ko", fp16=self.fp16, temperature=0.0,
                              condition_on_previous_text=False)
        return r["text"].strip()


SYSTEM = f"""너는 {OWNER} 님의 AI 비서다. {OWNER} 님이 시킨 용건으로 {OWNER} 님 전화기에서 전화를 걸어 통화하고 있다.

규칙
- 첫마디에 「{OWNER} 님 대신 전화드린 AI 비서」라고 밝힌다. 사람인 척하지 않는다.
- 한국어 존댓말, 한 번에 1~2문장. 전화 말투로 짧게. 기호 · 목록 · 이모지 금지.
- 모르는 사실(생년월일 · 주소 · 개통일 · 계좌 · 비밀번호 등)은 절대 지어내지 않는다.
- 다음 경우엔 대답 끝에 [본인연결] 을 붙인다: 본인확인 · 인증번호 · 결제 · 비밀번호를 요구할 때,
  상대가 본인/담당자와 통화하겠다고 할 때, 용건을 네가 더 진행할 수 없을 때.
  이때 말은 「네, {OWNER} 님을 바로 연결해 드리겠습니다. 잠시만 기다려 주세요.」
- ARS 안내가 들리면 말 대신 [번호:누를숫자] 만 쓴다(예: [번호:2]). 상담원 연결 번호를 우선 고른다.
- 용건이 끝났으면 인사하고 끝에 [끊기] 를 붙인다.
"""


class Brain:
    def __init__(self, goal):
        import anthropic
        key = open(KEY_FILE, encoding="utf-8").read().strip()
        self.c = anthropic.Anthropic(api_key=key)
        self.system = SYSTEM + f"\n이번 통화 용건: {goal}\n"
        self.msgs = []
        self.usage = {"in": 0, "cache_read": 0, "cache_write": 0, "out": 0}

    def reply(self, heard):
        self.msgs.append({"role": "user", "content": heard or "(상대가 전화를 받았다)"})
        with self.c.messages.stream(
            model=MODEL, max_tokens=300,
            system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "between_tools"}, output_config={"effort": "low"},
            messages=self.msgs,
        ) as s:
            msg = s.get_final_message()
        u = msg.usage
        self.usage["in"] += u.input_tokens; self.usage["out"] += u.output_tokens
        self.usage["cache_read"] += u.cache_read_input_tokens or 0
        self.usage["cache_write"] += u.cache_creation_input_tokens or 0
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        self.msgs.append({"role": "assistant", "content": text or "..."})
        return text

    def cost_usd(self):
        u = self.usage   # Sonnet 5.5: 입력 $2 · 캐시쓰기 ×1.25 · 캐시읽기 $0.20 · 출력 $10 (1M 토큰당)
        return (u["in"] * 2 + u["cache_write"] * 2.5 + u["cache_read"] * 0.2 + u["out"] * 10) / 1e6


# ── 본인 연결 ─────────────────────────────────────────
def notify_owner(summary):
    try:
        sys.path.insert(0, COMET)
        import notify
        notify.send(f"📞 본인 연결 필요\n{summary}")
    except Exception as e:
        log("텔레그램 실패:", e)
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1200, 250); time.sleep(0.15)
    except Exception:
        pass


def bridge(line):
    """호윤 PC 기본 마이크/스피커 ↔ 통화. 통화가 끝나면 멈춘다."""
    line.stop()
    def passthru(indata, outdata, frames, t, status):
        outdata[:] = indata
    up = sd.Stream(device=(sd.default.device[0], line.out_dev), samplerate=line.sr_out,
                   channels=1, dtype="float32", callback=passthru)
    down = sd.Stream(device=(line.in_dev, sd.default.device[1]), samplerate=line.sr_in,
                     channels=1, dtype="float32", callback=passthru)
    with up, down:
        log("본인 연결 — PC 마이크로 말하면 통화로 들어갑니다. 통화가 끝나면 자동으로 멈춥니다.")
        while in_call():
            time.sleep(1)


class Phone:
    """진짜 전화(S26+ · adb · 블루투스). 가짜 통화 시험(fake_call.py)은 같은 이름의 메서드를 가진 물건으로 바꿔 끼운다."""
    devices = staticmethod(hfp_devices)
    dial = staticmethod(dial)
    wait_audio = staticmethod(wait_call_audio)
    in_call = staticmethod(in_call)
    answered = staticmethod(answered)
    hangup = staticmethod(hangup)
    dtmf = staticmethod(dtmf)
    notify = staticmethod(notify_owner)
    bridge = staticmethod(bridge)
    Line = Line


# ── 통화 ─────────────────────────────────────────────
def run(number, goal, max_turns, test, phone=None):
    phone = phone or Phone()
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    transcript = []

    def note(who, text):
        line_ = f"[{time.strftime('%H:%M:%S')}] {who}: {text}"
        transcript.append(line_); log(f"{who}: {text}")

    in_dev, out_dev = phone.devices()
    if in_dev is None or out_dev is None:
        sys.exit("S26+ 핸즈프리 소리 장치가 없어요 — 블루투스 연결 확인")
    if phone.in_call():
        sys.exit("이미 통화 중이라 걸지 않았어요")
    log("받아쓰기 모델 올리는 중…"); ears = Ears()
    brain = Brain(goal + (" (시험 통화 — 시험 전화라고 밝히고 두세 마디 나눈 뒤 끊는다)" if test else ""))

    note("시스템", f"{number} 로 발신 (업무번호 {WORK_NUMBER}) · 용건: {goal}")
    phone.dial(number)
    in_dev, out_dev = phone.wait_audio()
    if in_dev is None:
        note("시스템", "통화 소리가 PC 로 안 넘어옴(핸즈프리 장치가 안 깨어남) — 끊음")
        phone.hangup()
        open(os.path.join(LOG_DIR, f"{stamp}_{number}.log"), "w", encoding="utf-8").write("\n".join(transcript) + "\n")
        return
    line = phone.Line(in_dev, out_dev)
    note("시스템", f"통화 소리 장치: 입력 {in_dev}({line.sr_in}Hz) · 출력 {out_dev}({line.sr_out}Hz)")
    line.start()

    handoff = False
    try:
        t0 = time.time()
        while True:                                 # 폰이 「연결됨(ACTIVE)」이 될 때까지 — 컬러링을 목소리로 착각하지 않게
            if not phone.in_call():
                note("시스템", "상대가 안 받거나 통화가 끊김"); return
            if time.time() - t0 > ANSWER_TIMEOUT:
                note("시스템", "응답 없음 — 끊음"); phone.hangup(); return
            a = phone.answered()
            if a != getattr(run, "_last_a", "x"):
                note("시스템", f"연결 감지 값: {a}"); run._last_a = a
            if a or (a is None and line.heard_voice.is_set()):
                break
            time.sleep(0.4)
        note("시스템", "상대가 받음")
        while not line.utter.empty():               # 받기 전에 쌓인 연결음 조각은 버린다
            line.utter.get_nowait()

        for _ in range(max_turns):
            try:
                heard = ears.text(line.utter.get(timeout=12), line.sr_in)
            except queue.Empty:
                heard = "(몇 초째 말이 없음)"
            if not phone.in_call():
                note("시스템", "상대가 끊음"); break
            note("상대", heard)
            t1 = time.time()
            say = brain.reply(heard)
            tags = re.findall(r"\[(본인연결|끊기|번호:[0-9*#]+)\]", say)
            spoken = re.sub(r"\[[^\]]*\]", "", say).strip()
            if spoken:
                pcm = tts(spoken, line.sr_out)
                note("코멧", f"{spoken}  (생각+목소리 {time.time()-t1:.1f}초)")
                line.play(pcm)
            for tg in tags:
                if tg.startswith("번호:"):
                    note("코멧", f"ARS {tg[3:]} 누름"); phone.dtmf(tg[3:])
            if "본인연결" in tags:
                handoff = True; break
            if "끊기" in tags:
                time.sleep(0.5); phone.hangup(); break
        else:
            note("시스템", f"{max_turns}번 넘게 이어져 본인 연결로 넘김"); handoff = True

        if handoff and phone.in_call():
            phone.notify(f"{number} · 용건: {goal}\n" + "\n".join(transcript[-4:]))
            if test:
                note("시스템", "시험 모드 — 본인 연결 대신 끊음"); phone.hangup()
            else:
                phone.bridge(line)
    finally:
        line.stop()
        note("시스템", f"API 비용 약 ${brain.cost_usd():.4f} · 토큰 {brain.usage}")
        path = os.path.join(LOG_DIR, f"{stamp}_{number}.log")
        open(path, "w", encoding="utf-8").write("\n".join(transcript) + "\n")
        log("기록:", path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("number")
    ap.add_argument("--goal", default="시험 통화")
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--test", action="store_true")
    a = ap.parse_args()
    run(re.sub(r"\D", "", a.number), a.goal, a.max_turns, a.test)


if __name__ == "__main__":
    main()
