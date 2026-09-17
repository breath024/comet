# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 음성 모듈
#   - Ear  : 듣기 (Whisper, 로컬, CPU)
#   - Mouth: 말하기 (Edge-TTS, InJoon, 매끄럽게 정리 후 출력)
#  무거운 의존성(whisper/torch)은 실제로 쓸 때만 로딩한다.
# ═══════════════════════════════════════════════════════════════
import os
import re
import sys
import json
import asyncio
import subprocess
import tempfile
import time
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
import edge_tts

# ── 말하기 설정 ──────────────────────────────────────────────
TTS_VOICE  = "ko-KR-SunHiNeural"
TTS_RATE   = "+10%"
TTS_GAIN   = 0.19          # 출력 음량 배수 (1.0=원본). 0.32는 너무 컸다 (2026-09-17)

# ── 아야카 목소리 (GPT-SoVITS 로 학습, 2026-09-17) ───────────
#   별도 폴더·별도 venv(A.B.B.Y.S/gpt-sovits)의 api_v2.py 서버를 HTTP 로 부른다.
#   서버가 안 떠 있으면 조용히 Edge-TTS(SunHi)로 떨어진다 — 말이 끊기지는 않는다.
TTS_ENGINE   = "ayaka"     # "ayaka" | "edge"
AYAKA_URL    = "http://127.0.0.1:9880/tts"
AYAKA_REF    = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
                   os.path.abspath(__file__)))), "gpt-sovits", "mydata", "ayaka_ref.json")
AYAKA_BOOST  = 1.7         # 아야카 출력이 Edge 보다 4.7dB 작다(-22.4 vs -17.7 LUFS) → 체감 음량 맞춤

# ── 형체(화면 HUD) 설정 ──────────────────────────────────────
SHOW_ORB  = True           # 말할 때 orb.py 를 띄운다. 끄려면 False
ORB_HOP   = 0.02           # 포락선 한 칸 = 20ms (50Hz)
_DIR      = os.path.dirname(os.path.abspath(__file__))

# ── 듣기 설정 ────────────────────────────────────────────────
STT_MODEL    = "turbo"     # large-v3-turbo. small 은 한국어를 자주 틀렸다("뉴스 거야"→"뉴스드럼이야")
#   2026-09-18 실측(정답 있는 한국어 12클립, GPU): small 글자오류 3.7% · medium 2.5% · turbo 1.3%(클립당 0.17초)
#   GPU 에선 반정밀도로 올려 상주 1.5GB (불러오는 순간만 4.6GB)
STT_PROMPT   = "코멧, 뉴스, 알림, 주식봇, 코인봇, 할일, 일정, 브리핑"   # 자주 쓰는 말 — 인식 쪽으로 기울인다
SAMPLE_RATE  = 16000


# ═══════════════════════════════════════════════════════════════
#  말하기 전 텍스트 정리 — 자비스처럼 흐르게
#  (쉼표·마크다운·코드블록이 끊김을 만들기 때문에 다듬는다)
# ═══════════════════════════════════════════════════════════════
def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " 자세한 코드는 화면을 봐. ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", "", text)              # 인라인 코드
    text = re.sub(r"https?://\S+", " 링크 ", text)   # URL
    text = re.sub(r"[#*_>~|\[\]()\-•·]", " ", text)  # 마크다운 기호·불릿
    text = text.replace("\n", " ")
    text = text.replace(",", " ").replace("、", " ")  # 쉼표 제거 → 끊김 완화
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ═══════════════════════════════════════════════════════════════
#  다른 소리 줄이기 (덕킹) — 코멧이 말하거나 내가 말하는 동안 다른 앱 볼륨을 낮춘다
#   윈도우 앱별 볼륨(pycaw). 겹쳐 불러도 되게 횟수를 센다(말걸기 창 안에서 말하기 등).
#   ★줄인 값을 파일에도 적어둔다 — 데몬이 도중에 죽으면 다음 시작 때 되돌린다.
#   ★되돌릴 때 지금 볼륨이 우리가 줄인 값 그대로일 때만 — 그 사이 내가 손댔으면 안 건드린다.
# ═══════════════════════════════════════════════════════════════
DUCK       = True
DUCK_LEVEL = 0.2          # 다른 앱을 원래 볼륨의 20% 로
_DUCK_FILE = os.path.join(_DIR, "duck_state.json")


class _Ducker:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.count = 0
        self.saved = []             # [(session, 원래볼륨, 줄인볼륨)]

    @staticmethod
    def _sessions():
        import comtypes
        try:
            comtypes.CoInitialize()     # 데몬 스레드마다 COM 초기화가 필요하다
        except OSError:
            pass
        from pycaw.pycaw import AudioUtilities
        me = os.getpid()
        return [s for s in AudioUtilities.GetAllSessions()
                if s.Process and s.ProcessId not in (0, me)]

    @staticmethod
    def _fade(pairs, steps=6, dt=0.025):
        for i in range(1, steps + 1):
            for vol, a, b in pairs:
                try:
                    vol.SetMasterVolume(a + (b - a) * i / steps, None)
                except Exception:
                    pass
            time.sleep(dt)

    def on(self):
        if not DUCK:
            return
        with self.lock:
            self.count += 1
            if self.count > 1:
                return
            try:
                saved, pairs = [], []
                for s in self._sessions():
                    v = s.SimpleAudioVolume
                    cur = v.GetMasterVolume()
                    if cur <= 0.01:
                        continue
                    low = round(cur * DUCK_LEVEL, 3)
                    saved.append((s, cur, low))
                    pairs.append((v, cur, low))
                self.saved = saved
                with open(_DUCK_FILE, "w", encoding="utf-8") as f:
                    json.dump([{"pid": s.ProcessId, "vol": c, "low": lo} for s, c, lo in saved], f)
                self._fade(pairs)
            except Exception as e:
                print(f"  [덕킹 오류] {e}")

    def off(self):
        if not DUCK:
            return
        with self.lock:
            if self.count == 0:
                return
            self.count -= 1
            if self.count > 0:
                return
            try:
                pairs = []
                for s, cur, low in self.saved:
                    v = s.SimpleAudioVolume
                    now = v.GetMasterVolume()
                    if abs(now - low) < 0.01:           # 그 사이 내가 바꿨으면 그대로 둔다
                        pairs.append((v, now, cur))
                self._fade(pairs)
            except Exception as e:
                print(f"  [덕킹 복구 오류] {e}")
            self.saved = []
            try:
                os.remove(_DUCK_FILE)
            except OSError:
                pass

    def recover(self):
        """지난번에 줄인 채로 죽었으면 되돌린다(데몬 시작 때 한 번)."""
        if not os.path.exists(_DUCK_FILE):
            return 0
        n = 0
        try:
            with open(_DUCK_FILE, encoding="utf-8") as f:
                rows = {r["pid"]: r for r in json.load(f)}
            for s in self._sessions():
                r = rows.get(s.ProcessId)
                v = s.SimpleAudioVolume
                if r and abs(v.GetMasterVolume() - r["low"]) < 0.01:
                    v.SetMasterVolume(r["vol"], None)
                    n += 1
        except Exception as e:
            print(f"  [덕킹 복구 오류] {e}")
        try:
            os.remove(_DUCK_FILE)
        except OSError:
            pass
        return n


ducker = _Ducker()


# ═══════════════════════════════════════════════════════════════
#  숫자 → 한글 읽기 (아야카용)
#   GPT-SoVITS 한국어 g2p 는 소수점·%·~ 를 제대로 못 읽는다 — "2.8%" 가 "이-" 로 나왔다(2026-09-18).
#   게다가 clean_for_speech 가 ~ · - 를 지우므로 그 전에 바꿔야 한다.
#   개수 단위(건·개·명…)는 고유어로 — "4건"을 "사건"으로 읽으면 事件 으로 들린다.
# ═══════════════════════════════════════════════════════════════
_SINO = "영일이삼사오육칠팔구"
_NATIVE_1 = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_10 = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
_COUNTERS = ("건", "개", "명", "시간", "시", "살", "번", "가지", "곳", "마리", "달", "군데", "사람", "대")


def _sino(n: int) -> str:
    if n == 0:
        return "영"
    units = ["", "십", "백", "천"]
    big = ["", "만", "억", "조", "경"]
    out, gi = [], 0
    while n > 0:
        chunk, n = n % 10000, n // 10000
        if chunk:
            s = ""
            for i in range(3, -1, -1):
                d = chunk // (10 ** i) % 10
                if d:
                    s += ("" if d == 1 and i > 0 else _SINO[d]) + units[i]
            if gi == 1 and s == "일":       # 10000 → 만 (일만 아님)
                s = ""
            out.append(s + big[gi])
        gi += 1
    return "".join(reversed(out))


def _native(n: int) -> str:
    if n == 20:
        return "스무"
    return _NATIVE_10[n // 10] + _NATIVE_1[n % 10]


def _read_int(digits: str, after: str) -> str:
    n = int(digits)
    if 0 < n < 100 and after.startswith(_COUNTERS) and not after.startswith(("개월", "대비", "달러")):
        return _native(n)
    return _sino(n)


def speak_numbers(text: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text)                         # 1,500 → 1500
    t = re.sub(r"(\d)\s*[~〜]\s*(?=[+\-]?\d)", r"\1에서 ", t)            # 3~4 → 3에서 4
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))\+(?=\d)", "플러스", t)
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))[-−](?=\d)", "마이너스", t)
    t = re.sub(r"\$\s?(\d[\d.]*)", r"\1달러", t)
    t = re.sub(r"%\s?p\b|%\s?포인트", "퍼센트포인트", t)
    t = t.replace("%", "퍼센트")

    # 소수는 붙여 읽는다(이점팔퍼센트) — 띄우면 끊겨 들린다. 뒤의 0은 안 읽는다(4.00 → 사, 3.50 → 삼점오)
    def dec(m):
        frac = m.group(2).rstrip("0")
        return _sino(int(m.group(1))) + ("점" + "".join(_SINO[int(c)] for c in frac) if frac else "")
    t = re.sub(r"(\d+)\.(\d+)", dec, t)
    t = re.sub(r"\d+", lambda m: _read_int(m.group(), t[m.end():m.end() + 2].lstrip()), t)
    return t


# ═══════════════════════════════════════════════════════════════
#  Mouth — Edge-TTS
# ═══════════════════════════════════════════════════════════════
def synth_mp3(text: str, voice: str = TTS_VOICE, rate: str = TTS_RATE) -> bytes:
    """텍스트 → InJoon mp3 바이트. 데몬이 폰으로 음성을 쏠 때 쓴다(로컬 재생 X)."""
    clean = clean_for_speech(text)
    if not clean:
        return b""

    async def _go():
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
        try:
            await edge_tts.Communicate(clean, voice=voice, rate=rate).save(path)
            with open(path, "rb") as fp:
                return fp.read()
        finally:
            if os.path.exists(path):
                os.remove(path)

    return asyncio.run(_go())


def envelope(data, sr, hop: float = ORB_HOP):
    """재생할 PCM → RMS 열(0~1). 형체가 이걸 읽어서 실제 소리에 맞춰 움직인다.
    스피커 루프백을 캡처할 필요가 없다 — 어차피 재생 직전에 파형을 손에 들고 있다."""
    x = data.mean(axis=1) if getattr(data, "ndim", 1) > 1 else data
    n = max(1, int(sr * hop))
    m = len(x) // n
    if m < 1:
        return []
    rms = np.sqrt((x[:m * n].reshape(m, n).astype(np.float64) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0:
        return []
    return [round(v, 3) for v in (rms / peak)]


def show_orb(data, sr, wait: float = 2.5):
    """형체를 띄우고 첫 프레임이 올라올 때까지만 잠깐 기다린다(소리와 시작을 맞추려고).
    실패해도 말하기는 그대로 진행한다 — 장식이 본업을 막으면 안 된다."""
    try:
        env = envelope(data, sr)
        if not env:
            return
        d = tempfile.mkdtemp(prefix="comet_orb_")
        ep, rp = os.path.join(d, "env.json"), os.path.join(d, "ready")
        with open(ep, "w", encoding="utf-8") as f:
            json.dump({"hop": ORB_HOP, "env": env}, f)
        py = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(py):
            py = sys.executable
        subprocess.Popen([py, os.path.join(_DIR, "orb.py"), "--env", ep, "--ready", rp],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        dead = time.time() + wait
        while time.time() < dead and not os.path.exists(rp):
            time.sleep(0.04)
    except Exception as e:
        print(f"  [형체 오류] {e}")


_AYAKA_SPAWNED = False


def start_ayaka_server():
    """아야카 서버를 창 없이 띄운다(안 기다림). 한 프로세스에서 한 번만 시도.
    모델 올리는 데 ~10초 — 그동안의 말은 Edge 로 나가고, 다음 말부터 아야카."""
    global _AYAKA_SPAWNED
    if _AYAKA_SPAWNED:
        return
    _AYAKA_SPAWNED = True
    root = os.path.dirname(os.path.dirname(AYAKA_REF))          # .../gpt-sovits
    py = os.path.join(root, ".venv", "Scripts", "pythonw.exe")
    if not os.path.exists(py):
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8",
               PYTHONPATH=root + os.pathsep + os.path.join(root, "GPT_SoVITS"))
    log = open(os.path.join(root, "mydata", "server.log"), "a", encoding="utf-8")
    subprocess.Popen([py, "api_v2.py", "-a", "127.0.0.1", "-p", "9880",
                      "-c", "mydata/ayaka_infer.yaml"],
                     cwd=root, env=env, stdout=log, stderr=log,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    print("  [아야카] 서버 띄우는 중 — 이번 말은 Edge 로")


def synth_ayaka(text: str, timeout: float = 30.0):
    """아야카 서버에 합성 요청 → (파형, 샘플레이트). 서버가 없거나 실패하면 None."""
    import io
    import urllib.request
    try:
        with open(AYAKA_REF, encoding="utf-8") as f:
            ref = json.load(f)
        body = json.dumps({
            "text": text, "text_lang": "ko",
            "ref_audio_path": ref["ref_audio_path"], "prompt_text": ref["prompt_text"],
            "prompt_lang": "ko", "text_split_method": "cut5",
            "media_type": "wav", "parallel_infer": False,   # 짧은 문장은 끄는 쪽이 빠르다(실측 1.7~2.0초)
        }).encode("utf-8")
        req = urllib.request.Request(AYAKA_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return sf.read(io.BytesIO(r.read()))
    except Exception as e:
        print(f"  [아야카 없음 → Edge 로] {str(e)[:60]}")
        if "10061" in str(e) or "refused" in str(e).lower():   # 서버가 아예 없을 때만 띄운다
            start_ayaka_server()
        return None


_hush = threading.Event()      # 말걸기 창 Esc — 읽는 중이면 끊고, 합성 중이면 안 읽는다


def stop_speaking():
    _hush.set()
    try:
        sd.stop()
    except Exception:
        pass


class Mouth:
    def __init__(self, voice: str = TTS_VOICE, rate: str = TTS_RATE, gain: float = TTS_GAIN):
        self.voice = voice
        self.rate  = rate
        self.gain  = gain

    def speak(self, text: str):
        if TTS_ENGINE == "ayaka":
            text = speak_numbers(text)       # clean_for_speech 가 ~·- 를 지우기 전에
        clean = clean_for_speech(text)
        if not clean:
            return
        _hush.clear()
        try:
            asyncio.run(self._speak(clean))
        except Exception as e:
            print(f"  [TTS 오류] {e}")

    async def _speak(self, text: str):
        path = None
        try:
            got = synth_ayaka(text) if TTS_ENGINE == "ayaka" else None
            if got is not None:
                data, sr = got
                data = data * self.gain * AYAKA_BOOST
            else:
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                    path = f.name
                await edge_tts.Communicate(text, voice=self.voice, rate=self.rate).save(path)
                data, sr = sf.read(path)
                data = data * self.gain          # 음량 조절
            if _hush.is_set():
                return
            ducker.on()                      # 다른 앱 소리 낮추고
            try:
                if SHOW_ORB:
                    show_orb(data, sr)       # 형체 먼저 띄우고(첫 프레임까지 대기)
                sd.play(data, sr)
                sd.wait()
            finally:
                ducker.off()
        finally:
            if path and os.path.exists(path):
                os.remove(path)


# ═══════════════════════════════════════════════════════════════
#  Ear — Whisper STT (마이크 녹음 + 무음 자동 종료)
# ═══════════════════════════════════════════════════════════════
class Ear:
    def __init__(self, model: str = STT_MODEL, sample_rate: int = SAMPLE_RATE):
        self.model_name  = model
        self.sample_rate = sample_rate
        self._model      = None
        self.last_stats  = {}

    def _load(self):
        if self._model is None:
            import whisper
            print(f"  [STT] Whisper '{self.model_name}' 로딩 중... (처음 한 번)")
            import torch
            self._cuda = torch.cuda.is_available()
            m = whisper.load_model(self.model_name, device="cuda" if self._cuda else "cpu")
            if self._cuda:
                m = m.half()
                for mod in m.modules():             # whisper 의 LayerNorm 은 float 가중치만 받는다
                    if isinstance(mod, torch.nn.LayerNorm):
                        mod.float()
            self._model = m
            print("  [STT] 준비 완료")

    def listen(self, stop_fn=None, start_timeout=None) -> str:
        """stop_fn() 이 참이 되면 바로 멈추고 "" (말걸기 창에서 타이핑을 시작했을 때).
        start_timeout 초 안에 말이 시작 안 되면 "" — 30초 내내 붙잡고 있지 않게."""
        self._load()
        audio = self._record(stop_fn=stop_fn, start_timeout=start_timeout)
        if self.last_stats.get("stopped") or not self.last_stats.get("started"):
            return ""
        if audio.size < self.sample_rate // 2:   # 0.5초 미만 = 무시
            return ""
        # 마이크 입력이 작으면(레벨·게인이 내려가 있으면) Whisper 가 문장을 뭉갠다 — 키워서 넣는다.
        # 배수는 20배까지만: 진짜 무음을 20배 키워 봐야 잡음만 커진다.
        peak = float(np.abs(audio).max())
        if 0 < peak < 0.3:
            gain = min(20.0, 0.3 / peak)
            audio = audio * gain
            self.last_stats["gain"] = round(gain, 1)
        result = self._model.transcribe(audio, fp16=getattr(self, "_cuda", False), language="ko",
                                        initial_prompt=STT_PROMPT)
        return result["text"].strip()

    # 문턱은 고정값이 아니라 '지금 이 방의 소음' 기준으로 잡는다.
    #   고정 0.015 는 조용한 방에서 작게 말하면 시작조차 못 하고(말해도 무음 취급),
    #   시끄러운 방에선 말을 멈춰도 계속 소리가 있어 안 끝났다.
    NOISE_START = 3.0      # 바닥소음의 이 배를 넘으면 '말 시작'
    NOISE_STOP  = 2.0      # 이 배 밑으로 내려가면 '조용'  (시작보다 낮게 = 말꼬리를 안 자름)
    PEAK_STOP   = 0.12     # 또는 지금까지 제일 큰 소리의 12% 밑 (소음이 커도 끝을 알아채게)
    FLOOR       = 0.0025   # 아무리 조용한 방이어도 이 밑은 소리로 안 본다
    WARMUP      = 3        # 마이크가 열리는 첫 칸들은 값이 0 에 가깝다 — 소음 추정에서 뺀다

    def _record(self, max_seconds=30, silence_duration=1.2, min_speech=0.4,
                stop_fn=None, start_timeout=None) -> np.ndarray:
        """끊김 없는 연속 녹음(InputStream) + 방 소음에 맞춘 자동 종료.

        ★예전엔 0.1초마다 sd.rec 로 스트림을 새로 열었다 — 칸과 칸 사이가 통째로 빠져
          15초를 받는 데 22.6초가 걸렸다(=말의 1/3 유실, 받아쓴 문장이 깨지던 원인).
          이제 콜백이 모든 샘플을 큐에 담고, 판정만 0.1초 단위로 본다.
        """
        import queue
        sr    = self.sample_rate
        chunk = int(sr * 0.1)
        q = queue.Queue()

        print("\n🎙  말해봐 (멈추면 자동 종료)...")
        frames, silent, spoke, started = [], 0, 0, False
        noise, peak = 0.0, 0.0
        hist = []                        # 최근 칸들의 크기 — 바닥소음을 계속 다시 잡는다
        self.last_stats = {"started": False, "stopped": False, "max_rms": 0.0, "sec": 0.0}
        silence_limit = int(silence_duration / 0.1)
        speech_min    = int(min_speech / 0.1)
        t_end   = time.time() + max_seconds
        t_start = time.time() + start_timeout if start_timeout else None

        with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=chunk,
                            callback=lambda indata, n_, t_, s_: q.put(indata[:, 0].copy())):
            while time.time() < t_end:
                if stop_fn and stop_fn():
                    self.last_stats["stopped"] = True
                    break
                if t_start and not started and time.time() > t_start:
                    break
                try:
                    block = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                frames.append(block)
                rms = float(np.sqrt(np.mean(block ** 2)))
                self.last_stats["max_rms"] = max(self.last_stats["max_rms"], round(rms, 4))
                peak = max(peak, rms)
                hist.append(rms)
                # 바닥소음 = 최근 5초 중 조용한 쪽 20% 지점. 말하는 중에도 숨 쉬는 틈이 여기 잡힌다.
                # 앞 WARMUP 칸(마이크 여는 중 = 거의 0)은 빼야 문턱이 바닥에 붙지 않는다.
                usable = hist[self.WARMUP:]
                if len(usable) >= 5:
                    noise = float(np.percentile(usable[-50:], 20))

                if not started:
                    # 소음 추정(0.8초)이 서기 전엔 시작 판정을 하지 않는다 —
                    # 문턱이 바닥에 붙어 잡음에 '시작'이 걸리면 start_timeout 이 풀려 30초를 붙잡는다.
                    if len(usable) >= 5 and rms > max(self.FLOOR, noise * self.NOISE_START):
                        started = True
                        spoke += 1
                        print("🔴 녹음 중...", end="\r")
                    continue

                gate = max(self.FLOOR, noise * self.NOISE_STOP, peak * self.PEAK_STOP)
                if rms > gate:
                    silent = 0
                    spoke += 1
                else:
                    silent += 1
                    if spoke >= speech_min and silent >= silence_limit:
                        break

        self.last_stats.update(started=started, sec=round(len(frames) * 0.1, 1),
                               noise=round(noise or 0, 4), peak=round(peak, 4))
        print("⏹  인식 중...        ")
        return np.concatenate(frames) if frames else np.zeros(1, dtype=np.float32)
