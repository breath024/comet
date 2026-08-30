"""
공통 레이어 — main.py / ai_core.py 양쪽에서 import해서 씀
"""
import json
import numpy as np
import sounddevice as sd
import soundfile as sf
import whisper
import librosa
import warnings
import os
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime

warnings.filterwarnings("ignore")

CONFIG_DEFAULTS = {
    "sample_rate":      16000,
    "max_seconds":      30,
    "silence_thresh":   0.015,
    "silence_duration": 0.8,
    "min_speech":       0.5,
}


# ═══════════════════════════════════════════════════════
#  출력 구조체
# ═══════════════════════════════════════════════════════
@dataclass
class AIOutput:
    text:          str
    emotion:       str
    intensity:     float
    inner_thought: Optional[str]
    confidence:    float = 0.8
    timestamp:     str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════
#  음성 특징 분석 레이어
# ═══════════════════════════════════════════════════════
class VoiceAnalyzer:
    def analyze(self, audio: np.ndarray, sr: int, silence_thresh: float = 0.015) -> dict:
        result = {
            "pitch_mean":    0.0,
            "pitch_std":     0.0,
            "rms_mean":      0.0,
            "rms_std":       0.0,
            "speech_rate":   0.0,
            "silence_ratio": 0.0,
            "tremor":        False,
            "summary":       ""
        }
        try:
            y = audio.astype(np.float32)
            f0, voiced_flag, _ = librosa.pyin(y, fmin=80, fmax=400, sr=sr)
            voiced = f0[voiced_flag]
            if len(voiced) > 0:
                result["pitch_mean"] = float(np.mean(voiced))
                result["pitch_std"]  = float(np.std(voiced))
                result["tremor"] = result["pitch_std"] > 15.0

            rms = librosa.feature.rms(y=y)[0]
            result["rms_mean"] = float(np.mean(rms))
            result["rms_std"]  = float(np.std(rms))

            silent_frames = np.sum(rms < silence_thresh)
            result["silence_ratio"] = float(silent_frames / len(rms))

            zcr = librosa.feature.zero_crossing_rate(y)[0]
            result["speech_rate"] = float(np.clip(np.mean(zcr) * 10, 0, 1))

            notes = []
            if result["tremor"]:                   notes.append("목소리 떨림")
            if result["silence_ratio"] > 0.5:      notes.append("침묵 많음(망설임?)")
            if result["speech_rate"] > 0.7:        notes.append("빠른 말투")
            elif result["speech_rate"] < 0.3:      notes.append("느린 말투")
            if result["rms_std"] > 0.02:           notes.append("감정 기복")
            result["summary"] = ", ".join(notes) if notes else "특이사항 없음"

        except Exception as e:
            result["summary"] = f"분석 실패: {e}"

        return result


# ═══════════════════════════════════════════════════════
#  STT 레이어 (Whisper)
# ═══════════════════════════════════════════════════════
class SpeechToText:
    def __init__(self, config: dict):
        self.config = config
        print("[STT] Whisper 모델 로딩 중... (처음 한 번만)")
        self.model    = whisper.load_model(config["whisper_model"])
        self.analyzer = VoiceAnalyzer()
        print("[STT] 로딩 완료!")

    def record(self) -> tuple[np.ndarray, int]:
        sr               = self.config["sample_rate"]
        silence_thresh   = self.config["silence_thresh"]
        silence_duration = self.config["silence_duration"]
        min_speech       = self.config["min_speech"]

        chunk_size    = int(sr * 0.1)
        max_chunks    = int(self.config["max_seconds"] / 0.1)
        silence_limit = int(silence_duration / 0.1)
        speech_min    = int(min_speech / 0.1)

        print("\n🎙  말해봐 (멈추면 자동 종료)...")

        frames        = []
        silent_chunks = 0
        speech_chunks = 0
        started       = False

        for _ in range(max_chunks):
            chunk = sd.rec(chunk_size, samplerate=sr, channels=1, dtype="float32")
            sd.wait()
            chunk = chunk.flatten()
            frames.append(chunk)

            rms = float(np.sqrt(np.mean(chunk ** 2)))

            if rms > silence_thresh:
                silent_chunks = 0
                speech_chunks += 1
                if not started:
                    started = True
                    print("🔴 녹음 중...", end="\r")
            else:
                if started:
                    silent_chunks += 1
                    if speech_chunks >= speech_min and silent_chunks >= silence_limit:
                        print("⏹  감지 완료!       ")
                        break

        return np.concatenate(frames), sr

    def transcribe(self) -> tuple[str, dict]:
        audio, sr      = self.record()
        voice_features = self.analyzer.analyze(audio, sr, self.config["silence_thresh"])
        result         = self.model.transcribe(audio, fp16=False, language="ko")
        text           = result["text"].strip()
        return text, voice_features


# ═══════════════════════════════════════════════════════
#  대화 기록 관리
# ═══════════════════════════════════════════════════════
class HistoryManager:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.history  = []
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.history = json.load(f)
                print(f"[기억] 이전 대화 {len(self.history)}개 불러옴")
            except Exception:
                self.history = []

    def save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    def add(self, role: str, content: str, autosave: bool = False):
        """autosave=True일 때만 즉시 저장 — 턴 마지막(assistant)에만 True로 호출"""
        self.history.append({"role": role, "content": content})
        if autosave:
            self.save()

    def reset(self):
        self.history = []
        if os.path.exists(self.filepath):
            os.remove(self.filepath)
        print("[기억] 초기화 완료")

    def get(self) -> list:
        return self.history
