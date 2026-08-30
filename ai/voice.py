# -*- coding: utf-8 -*-
# ═══════════════════════════════════════════════════════════════
#  COMET 음성 모듈
#   - Ear  : 듣기 (Whisper, 로컬, CPU)
#   - Mouth: 말하기 (Edge-TTS, InJoon, 매끄럽게 정리 후 출력)
#  무거운 의존성(whisper/torch)은 실제로 쓸 때만 로딩한다.
# ═══════════════════════════════════════════════════════════════
import os
import re
import asyncio
import tempfile
import numpy as np
import sounddevice as sd
import soundfile as sf
import edge_tts

# ── 말하기 설정 ──────────────────────────────────────────────
TTS_VOICE  = "ko-KR-InJoonNeural"
TTS_RATE   = "+25%"
TTS_GAIN   = 0.55          # 출력 음량 배수 (1.0=원본). 실행 중 조절 가능

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


class Mouth:
    def __init__(self, voice: str = TTS_VOICE, rate: str = TTS_RATE, gain: float = TTS_GAIN):
        self.voice = voice
        self.rate  = rate
        self.gain  = gain

    def speak(self, text: str):
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
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                path = f.name
            await edge_tts.Communicate(text, voice=self.voice, rate=self.rate).save(path)
            data, sr = sf.read(path)
            data = data * self.gain          # 음량 조절
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
