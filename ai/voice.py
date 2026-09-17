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
STT_MODEL    = "small"     # 캐시됨. 더 정확히: medium (느림)
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
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))\+(?=\d)", "플러스 ", t)
    t = re.sub(r"(?:(?<=^)|(?<=[\s(]))[-−](?=\d)", "마이너스 ", t)
    t = re.sub(r"\$\s?(\d[\d.]*)", r"\1달러", t)
    t = re.sub(r"%\s?p\b|%\s?포인트", " 퍼센트포인트", t)
    t = t.replace("%", " 퍼센트")

    def dec(m):
        return _sino(int(m.group(1))) + " 점 " + "".join(_SINO[int(c)] for c in m.group(2))
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
            if SHOW_ORB:
                show_orb(data, sr)           # 형체 먼저 띄우고(첫 프레임까지 대기)
            sd.play(data, sr)
            sd.wait()
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

    def _load(self):
        if self._model is None:
            import whisper
            print(f"  [STT] Whisper '{self.model_name}' 로딩 중... (처음 한 번)")
            self._model = whisper.load_model(self.model_name)
            print("  [STT] 준비 완료")

    def listen(self) -> str:
        self._load()
        audio = self._record()
        if audio.size < self.sample_rate // 2:   # 0.5초 미만 = 무시
            return ""
        result = self._model.transcribe(audio, fp16=False, language="ko")
        return result["text"].strip()

    def _record(self, max_seconds=30, silence_thresh=0.015,
                silence_duration=1.2, min_speech=0.4) -> np.ndarray:
        sr            = self.sample_rate
        chunk         = int(sr * 0.1)
        max_chunks    = int(max_seconds / 0.1)
        silence_limit = int(silence_duration / 0.1)
        speech_min    = int(min_speech / 0.1)

        print("\n🎙  말해봐 (멈추면 자동 종료)...")
        frames, silent, spoke, started = [], 0, 0, False

        for _ in range(max_chunks):
            block = sd.rec(chunk, samplerate=sr, channels=1, dtype="float32")
            sd.wait()
            block = block.flatten()
            frames.append(block)
            rms = float(np.sqrt(np.mean(block ** 2)))

            if rms > silence_thresh:
                silent = 0
                spoke += 1
                if not started:
                    started = True
                    print("🔴 녹음 중...", end="\r")
            elif started:
                silent += 1
                if spoke >= speech_min and silent >= silence_limit:
                    break

        print("⏹  인식 중...        ")
        return np.concatenate(frames) if frames else np.zeros(1, dtype=np.float32)
